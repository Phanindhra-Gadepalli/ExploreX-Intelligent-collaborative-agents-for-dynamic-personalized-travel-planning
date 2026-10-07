"""
services/email_service.py — ExploreX itinerary email delivery.

Responsibilities
----------------
1. Normalize whatever itinerary / budget shape the pipeline produced into a
   single canonical structure (the pipeline can emit several shapes: route-agent
   day dicts with ``spots``, frontend "bucket" days with morning/afternoon/
   evening/meals, strategy plans that are plain lists of attraction-name
   strings, or a ``{"days": [...]}`` wrapper).
2. Render a branded, comprehensive HTML + plain-text email.
3. Send it over SMTP using credentials from environment variables only.

Nothing here ever raises to the caller for expected problems (bad input, SMTP
failure): it logs with an ``[EMAIL]`` prefix and returns ``False``. Credentials
and stack traces are never returned to the frontend.
"""

import os
import re
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass


# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

_EMAIL_RE = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]{2,}$")


def is_valid_email(email) -> bool:
    """Return True when *email* looks like a deliverable address."""
    return bool(email) and isinstance(email, str) and bool(_EMAIL_RE.match(email.strip()))


# ---------------------------------------------------------------------------
# Itinerary normalization
# ---------------------------------------------------------------------------

def _as_text(value) -> str:
    """Best-effort conversion of a scalar/dict activity into a readable name."""
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, dict):
        for key in ("name", "title", "label", "activity", "spot", "text"):
            if value.get(key):
                return str(value[key]).strip()
        # Fall back to the first non-empty scalar value.
        for v in value.values():
            if isinstance(v, (str, int, float)) and str(v).strip():
                return str(v).strip()
    return str(value).strip()


def _first(d: dict, *keys, default=""):
    for k in keys:
        if isinstance(d, dict) and d.get(k) not in (None, ""):
            return d.get(k)
    return default


def _normalize_activity(raw, bucket_label="") -> dict:
    """Convert one activity (dict or string) into a canonical dict."""
    if isinstance(raw, str):
        return {
            "name": raw.strip(),
            "start_time": "",
            "end_time": "",
            "category": bucket_label,
            "description": "",
        }
    if isinstance(raw, dict):
        name = _as_text(raw) or _first(raw, "name", "title", "label")
        return {
            "name": str(name).strip(),
            "start_time": str(_first(raw, "start_time", "time", "from", "start")).strip(),
            "end_time": str(_first(raw, "end_time", "until", "to", "end")).strip(),
            "category": str(_first(raw, "category", "type", "kind") or bucket_label).strip(),
            "description": str(_first(raw, "description", "desc", "details", "note")).strip(),
        }
    text = _as_text(raw)
    return {"name": text, "start_time": "", "end_time": "", "category": bucket_label, "description": ""}


_DAY_PREFIX_RE = re.compile(r"^\s*day\s*([0-9]+)\b[\s:.\-)]*", re.IGNORECASE)


def _activities_from_day_dict(day: dict) -> list:
    """Pull a flat activity list out of a day dict in any known shape."""
    acts = []
    # Explicit list keys first.
    for key in ("spots", "activities", "items", "schedule", "plan", "events"):
        val = day.get(key)
        if isinstance(val, list) and val:
            acts.extend(_normalize_activity(v) for v in val)
    # Time-of-day buckets used by the frontend renderer.
    for bucket in ("morning", "afternoon", "evening", "night", "meals"):
        val = day.get(bucket)
        if isinstance(val, list) and val:
            label = bucket.capitalize() if bucket != "meals" else "Food"
            acts.extend(_normalize_activity(v, bucket_label=label) for v in val)
    # A day dict that IS a single activity (has a name but no nested list).
    if not acts and _as_text(day):
        acts.append(_normalize_activity(day))
    return acts


def normalize_itinerary(raw) -> list:
    """
    Normalize any supported itinerary shape into a canonical list:

        [{"day": int, "date": str, "activities": [
            {"name", "start_time", "end_time", "category", "description"}]}]

    Handles: None, ``{"days": [...]}``, list of day dicts (spots / buckets),
    list of plain strings ("Day 1: ..."), and mixed/nested variants. Never
    raises — malformed input is coerced or skipped.
    """
    if not raw:
        return []

    # Unwrap a {"days": [...]} (or similar) container.
    if isinstance(raw, dict):
        for key in ("days", "itinerary", "schedule", "plan"):
            if isinstance(raw.get(key), list):
                raw = raw[key]
                break
        else:
            # A lone dict that looks like a single day.
            raw = [raw]

    if not isinstance(raw, list):
        return []

    days = []
    auto_day = 0
    for entry in raw:
        auto_day += 1
        day_number = auto_day
        date_str = ""
        activities = []

        if isinstance(entry, dict):
            try:
                if entry.get("day") is not None:
                    day_number = int(re.search(r"\d+", str(entry["day"])).group())
            except (AttributeError, ValueError):
                pass
            date_str = str(_first(entry, "date", "day_date", "calendar_date"))
            activities = _activities_from_day_dict(entry)
        elif isinstance(entry, str):
            text = entry.strip()
            if not text:
                continue
            m = _DAY_PREFIX_RE.match(text)
            if m:
                day_number = int(m.group(1))
                text = text[m.end():].strip()
            # Split a packed day string into separate activities.
            parts = [p.strip() for p in re.split(r"\n|;|\s\|\s", text) if p.strip()]
            activities = [_normalize_activity(p) for p in (parts or [text])]
        else:
            activities = [_normalize_activity(entry)]

        activities = [a for a in activities if a.get("name")]
        if not activities and not date_str:
            # Nothing usable in this entry — skip it but keep numbering sane.
            continue
        days.append({"day": day_number, "date": date_str, "activities": activities})

    # Re-number sequentially if day numbers ended up duplicated/missing.
    seen = set()
    for i, d in enumerate(days, start=1):
        if d["day"] in seen:
            d["day"] = i
        seen.add(d["day"])
    return days


# ---------------------------------------------------------------------------
# Budget normalization
# ---------------------------------------------------------------------------

def _to_number(value):
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return value
    m = re.search(r"[-+]?\d[\d,]*\.?\d*", str(value))
    if not m:
        return None
    try:
        return float(m.group().replace(",", ""))
    except ValueError:
        return None


def normalize_budget(budget) -> dict:
    """Coerce the many budget shapes into {total, accommodation, food,
    transport, attractions, currency, warning, infeasible}."""
    out = {
        "total": None, "accommodation": None, "food": None,
        "transport": None, "attractions": None,
        "currency": "₹", "warning": "", "infeasible": False,
    }
    if not isinstance(budget, dict):
        return out

    breakdown = budget.get("breakdown") if isinstance(budget.get("breakdown"), dict) else {}
    out["currency"] = budget.get("currency_symbol") or budget.get("currency") or "₹"
    if out["currency"] in ("INR", "inr"):
        out["currency"] = "₹"

    out["total"] = _to_number(budget.get("total", budget.get("total_cost", budget.get("grand_total"))))
    out["accommodation"] = _to_number(budget.get("accommodation", breakdown.get("accommodation", breakdown.get("hotels", breakdown.get("stay")))))
    out["food"] = _to_number(budget.get("food", breakdown.get("food", breakdown.get("meals"))))
    out["transport"] = _to_number(budget.get("transport", breakdown.get("local_transport", breakdown.get("transport", breakdown.get("transit")))))
    out["attractions"] = _to_number(budget.get("attractions", breakdown.get("attractions", breakdown.get("activities"))))
    out["warning"] = str(budget.get("budget_warning") or budget.get("warning") or "")
    out["infeasible"] = bool(budget.get("budget_infeasible", budget.get("infeasible", False)))
    return out


def _money(value, symbol="₹") -> str:
    n = _to_number(value)
    if n is None:
        return f"{symbol}0"
    return f"{symbol}{n:,.0f}"


# ---------------------------------------------------------------------------
# Rendering
# ---------------------------------------------------------------------------

def _esc(s) -> str:
    """Minimal HTML escaping for untrusted text interpolated into the email."""
    return (str(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


# Short alias used throughout the render helpers below.
esc = _esc


def _render_itinerary_html(days) -> str:
    if not days:
        return "<p style='color:#666;'>No detailed itinerary available.</p>"
    blocks = []
    for day in days:
        rows = ""
        for a in day.get("activities", []):
            when = ""
            if a.get("start_time"):
                when = a["start_time"] + (f" – {a['end_time']}" if a.get("end_time") else "")
            elif a.get("end_time"):
                when = a["end_time"]
            cat = (f"<span style='color:#888;font-size:0.85em;'>({_esc(a['category'])})</span>"
                   if a.get("category") else "")
            time_html = f"<strong style='color:#0b6bcb;'>{_esc(when)}</strong> " if when else ""
            desc_html = (f"<div style='color:#666;font-size:0.9em;'>{_esc(a['description'])}</div>"
                         if a.get("description") else "")
            rows += f"<li style='margin-bottom:6px;'>{time_html}{_esc(a['name'])} {cat}{desc_html}</li>"
        date_str = _esc(day['date'])
        if day.get("is_suggested_date"):
            date_str += " (Suggested)"
        date_txt = (f" <span style='color:#888;font-weight:normal;font-size:0.9em;'>• "
                    f"{date_str}</span>" if day.get("date") else "")
        blocks.append(
            f"<div style='margin-bottom:18px;'>"
            f"<h4 style='margin:0 0 6px;color:#0b6bcb;'>Day {_esc(day['day'])}{date_txt}</h4>"
            f"<ul style='margin:0;padding-left:20px;'>{rows}</ul></div>"
        )
    return "".join(blocks)


def _render_itinerary_text(days) -> str:
    if not days:
        return "No detailed itinerary available.\n"
    lines = []
    for day in days:
        date_str = day['date']
        if day.get("is_suggested_date"):
            date_str += " (Suggested)"
        head = f"Day {day['day']}" + (f" ({date_str})" if day.get("date") else "")
        lines.append(f"\n{head}\n" + "-" * len(head))
        for a in day.get("activities", []):
            when = ""
            if a.get("start_time"):
                when = a["start_time"] + (f"-{a['end_time']}" if a.get("end_time") else "")
            elif a.get("end_time"):
                when = a["end_time"]
            prefix = f"  {when}: " if when else "  - "
            cat = f" [{a['category']}]" if a.get("category") else ""
            lines.append(f"{prefix}{a['name']}{cat}")
            if a.get("description"):
                lines.append(f"      {a['description']}")
    return "\n".join(lines) + "\n"


def _render_list_section(title, items, icon="•"):
    """Render an optional section (accommodation / food) from a list of POIs."""
    names = []
    for it in items or []:
        if isinstance(it, dict):
            nm = _first(it, "name", "title", "label")
            price = _first(it, "price", "price_per_night", "cost")
            if nm:
                names.append(f"{nm}" + (f" — {price}" if price else ""))
        elif isinstance(it, str) and it.strip():
            names.append(it.strip())
    if not names:
        return "", ""
    html = (f"<h4 style='margin:18px 0 6px;color:#0b6bcb;'>{icon} {esc(title)}</h4>"
            f"<ul style='margin:0 0 10px;padding-left:20px;'>"
            + "".join(f"<li>{esc(n)}</li>" for n in names[:12]) + "</ul>")
    text = f"\n{title}:\n" + "\n".join(f"  - {n}" for n in names[:12]) + "\n"
    return html, text


def _render_transit(transit) -> tuple:
    """Render transportation options from the transit-agent structure."""
    if not transit:
        return "", ""
    options = []
    if isinstance(transit, dict):
        options = transit.get("options") if isinstance(transit.get("options"), list) else []
        recommended = transit.get("recommended_mode")
    elif isinstance(transit, list):
        options = transit
        recommended = None
    else:
        return "", ""

    rows_html = ""
    rows_text = ""
    for opt in options[:8]:
        if not isinstance(opt, dict):
            continue
        mode = _first(opt, "mode", "type")
        provider = _first(opt, "provider", "name", "operator")
        duration = _first(opt, "duration", "travel_time")
        cost = _first(opt, "estimated_cost", "cost", "price")
        dep = _first(opt, "departure_info", "departure")
        arr = _first(opt, "arrival_info", "arrival")
        star = " ⭐" if recommended and str(mode).lower() == str(recommended).lower() else ""
        line = f"{mode}{star}: {provider}"
        detail = []
        if duration:
            detail.append(f"{duration}")
        if dep or arr:
            detail.append(f"{dep} → {arr}")
        if cost:
            detail.append(f"~₹{_to_number(cost):,.0f}" if _to_number(cost) else str(cost))
        if detail:
            line += " (" + ", ".join(str(d) for d in detail if d) + ")"
        rows_html += f"<li style='margin-bottom:4px;'>{esc(line)}</li>"
        rows_text += f"  - {line}\n"

    if not rows_html:
        return "", ""
    html = ("<h4 style='margin:18px 0 6px;color:#0b6bcb;'>🚆 Getting There</h4>"
            f"<ul style='margin:0 0 10px;padding-left:20px;'>{rows_html}</ul>")
    text = "\nTransportation:\n" + rows_text
    return html, text


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def send_trip_email(to_email, user_name, city, itinerary, budget, confirmation,
                    notes="", trip_context=None):
    """
    Send the trip itinerary email.

    Parameters
    ----------
    to_email      : recipient address (validated).
    user_name     : traveler display name.
    city          : destination.
    itinerary     : ANY supported itinerary shape (normalized internally).
    budget        : budget dict (normalized internally).
    confirmation  : short intro line.
    notes         : optional user travel notes.
    trip_context  : optional dict with origin, days, people, kids, start_date,
                    end_date, accommodation, food, transport, tips.

    Returns True on success, False otherwise. Never raises for expected errors.
    """
    ctx = trip_context or {}

    # ---- Validation -------------------------------------------------------
    if not is_valid_email(to_email):
        print(f"[EMAIL][VALIDATION] Rejected invalid recipient: {to_email!r}")
        return False
    to_email = to_email.strip()

    smtp_server = os.environ.get("MAIL_SERVER")
    smtp_port = os.environ.get("MAIL_PORT")
    smtp_username = os.environ.get("MAIL_USERNAME")
    smtp_password = os.environ.get("MAIL_PASSWORD")
    use_tls = str(os.environ.get("MAIL_USE_TLS", "True")).lower() == "true"

    if not smtp_server or not smtp_username:
        print(f"[EMAIL][SMTP] Credentials not configured (MAIL_SERVER/MAIL_USERNAME). "
              f"Cannot send to {to_email}.")
        return False

    # ---- Normalize inputs -------------------------------------------------
    days = normalize_itinerary(itinerary)
    bud = normalize_budget(budget)
    symbol = bud.get("currency", "₹")

    origin = ctx.get("origin") or ""
    duration = ctx.get("days") or (len(days) if days else "")
    people = ctx.get("people")
    kids = ctx.get("kids")
    start_date = ctx.get("start_date") or ""
    end_date = ctx.get("end_date") or ""
    tips = ctx.get("tips") or ""

    travelers_txt = ""
    if people not in (None, ""):
        travelers_txt = f"{people} traveler" + ("s" if str(people) != "1" else "")
        if kids not in (None, "", 0, "0"):
            travelers_txt += f" ({kids} kid" + ("s" if str(kids) != "1" else "") + ")"

    dates_txt = ""
    if start_date and start_date.lower() not in ("flexible", "not decided", "none"):
        dates_txt = start_date + (f" → {end_date}" if end_date else "")

    print(f"[EMAIL][PAYLOAD] to={to_email} city={city!r} origin={origin!r} "
          f"days={duration} people={people} kids={kids} itinerary_days={len(days)} "
          f"budget_total={bud.get('total')} notes={'yes' if notes else 'no'}")

    if not days:
        print("[EMAIL][PAYLOAD] Itinerary empty after normalization — email will note that.")

    # ---- Optional sections ------------------------------------------------
    acc_html, acc_text = _render_list_section(
        "Where You'll Stay",
        ctx.get("accommodation"),
        icon="🏨",
    )
    food_html, food_text = _render_list_section(
        "Food Recommendations",
        ctx.get("food"),
        icon="🍽️",
    )
    transit_html, transit_text = _render_transit(ctx.get("transport"))

    # ---- Plain-text version ----------------------------------------------
    text = f"Hello {user_name},\n\n{confirmation}\n\n"
    text += "=========================================\n"
    text += f"  ExploreX — Your Trip to {city}\n"
    text += "=========================================\n\n"
    if origin:
        text += f"From: {origin}\n"
    if duration:
        text += f"Duration: {duration} day" + ("s" if str(duration) != "1" else "") + "\n"
    if travelers_txt:
        text += f"Travelers: {travelers_txt}\n"
    if dates_txt:
        text += f"Travel dates: {dates_txt}\n"
    text += "\n"

    text += "Estimated Budget:\n"
    text += f"  Total:         {_money(bud['total'], symbol)}\n"
    text += f"  Accommodation: {_money(bud['accommodation'], symbol)}\n"
    text += f"  Food:          {_money(bud['food'], symbol)}\n"
    text += f"  Transport:     {_money(bud['transport'], symbol)}\n"
    text += f"  Activities:    {_money(bud['attractions'], symbol)}\n"
    if bud.get("warning"):
        text += f"  Note: {bud['warning']}\n"
    text += "\n"

    text += "Itinerary:\n" + _render_itinerary_text(days)
    if acc_text:
        text += acc_text
    if food_text:
        text += food_text
    if transit_text:
        text += transit_text
    if tips:
        text += f"\nTravel Tips:\n{tips}\n"
    if notes:
        text += f"\nYour Notes:\n{notes}\n"
    text += "\nHappy Travels!\nThe ExploreX Team\n"

    # ---- HTML version -----------------------------------------------------
    summary_rows = ""
    if origin:
        summary_rows += f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>From</td><td style='padding:4px 0;'><strong>{esc(origin)}</strong></td></tr>"
    if duration:
        summary_rows += f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>Duration</td><td style='padding:4px 0;'><strong>{esc(duration)} day{'s' if str(duration) != '1' else ''}</strong></td></tr>"
    if travelers_txt:
        summary_rows += f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>Travelers</td><td style='padding:4px 0;'><strong>{esc(travelers_txt)}</strong></td></tr>"
    if dates_txt:
        summary_rows += f"<tr><td style='padding:4px 12px 4px 0;color:#666;'>Travel dates</td><td style='padding:4px 0;'><strong>{esc(dates_txt)}</strong></td></tr>"

    budget_rows = "".join(
        f"<tr><td style='padding:4px 16px 4px 0;color:#666;'>{label}</td>"
        f"<td style='padding:4px 0;font-weight:600;'>{_money(val, symbol)}</td></tr>"
        for label, val in (
            ("Total", bud["total"]), ("Accommodation", bud["accommodation"]),
            ("Food", bud["food"]), ("Transport", bud["transport"]),
            ("Activities", bud["attractions"]),
        )
    )
    warning_html = (f"<p style='color:#b45309;background:#fffbeb;padding:8px 12px;"
                    f"border-left:4px solid #f59e0b;border-radius:4px;'>⚠️ {esc(bud['warning'])}</p>"
                    if bud.get("warning") else "")
    notes_html = (f"<h4 style='margin:18px 0 6px;color:#0b6bcb;'>📝 Your Notes</h4>"
                  f"<p style='white-space:pre-wrap;background:#f8fafc;padding:10px;"
                  f"border-radius:6px;'>{esc(notes)}</p>" if notes else "")
    tips_html = (f"<h4 style='margin:18px 0 6px;color:#0b6bcb;'>💡 Travel Tips</h4>"
                 f"<p style='white-space:pre-wrap;background:#f0f9ff;padding:10px;"
                 f"border-radius:6px;'>{esc(tips)}</p>" if tips else "")

    html = f"""<!DOCTYPE html>
<html><body style="margin:0;padding:0;background:#eef2f7;font-family:Arial,Helvetica,sans-serif;color:#222;line-height:1.6;">
  <div style="max-width:640px;margin:0 auto;background:#ffffff;">
    <div style="background:linear-gradient(135deg,#0b6bcb,#12b3a6);padding:28px 24px;text-align:center;">
      <div style="color:#ffffff;font-size:26px;font-weight:800;letter-spacing:1px;">ExploreX</div>
      <div style="color:#e6f4ff;font-size:13px;letter-spacing:2px;text-transform:uppercase;margin-top:4px;">Your Journey, Planned</div>
    </div>
    <div style="padding:24px;">
      <h2 style="margin:0 0 6px;color:#111;">Hello {esc(user_name)},</h2>
      <p style="margin:0 0 18px;color:#444;">{esc(confirmation)}</p>
      <h3 style="margin:0 0 10px;color:#0b6bcb;">Your Trip to {esc(city)}</h3>
      <table style="border-collapse:collapse;margin-bottom:16px;font-size:15px;">{summary_rows}</table>

      <div style="background:#f4f7fb;padding:14px 16px;border-radius:8px;margin-bottom:20px;">
        <h4 style="margin:0 0 8px;color:#111;">Estimated Budget</h4>
        <table style="border-collapse:collapse;font-size:15px;">{budget_rows}</table>
        {warning_html}
      </div>

      <h3 style="margin:0 0 10px;color:#111;">Your Itinerary</h3>
      {_render_itinerary_html(days)}
      {acc_html}
      {food_html}
      {transit_html}
      {tips_html}
      {notes_html}

      <p style="margin-top:28px;color:#444;">Happy Travels!<br><strong>The ExploreX Team</strong></p>
    </div>
    <div style="background:#0f172a;color:#94a3b8;text-align:center;padding:16px;font-size:12px;">
      You received this email because you planned a trip with ExploreX.
    </div>
  </div>
</body></html>"""

    # ---- Send -------------------------------------------------------------
    try:
        msg = MIMEMultipart("alternative")
        msg["Subject"] = f"Your ExploreX Trip to {city} is Ready!"
        msg["From"] = f"ExploreX Travel <{smtp_username}>"
        msg["To"] = to_email
        msg.attach(MIMEText(text, "plain"))
        msg.attach(MIMEText(html, "html"))

        port = int(smtp_port) if smtp_port else (587 if use_tls else 465)
        print(f"[EMAIL][SMTP] Connecting to {smtp_server}:{port} (tls={use_tls}) for {to_email}")

        if use_tls:
            server = smtplib.SMTP(smtp_server, port, timeout=30)
            server.ehlo()
            server.starttls()
            server.ehlo()
        else:
            server = smtplib.SMTP_SSL(smtp_server, port, timeout=30)

        if smtp_password:
            server.login(smtp_username, smtp_password.replace(" ", ""))

        server.sendmail(smtp_username, to_email, msg.as_string())
        server.quit()
        print(f"[EMAIL][SUCCESS] Itinerary emailed to {to_email} ({len(days)} day(s)).")
        return True

    except smtplib.SMTPAuthenticationError:
        print("[EMAIL][ERROR] SMTP authentication failed — check MAIL_USERNAME / MAIL_PASSWORD (app password).")
        return False
    except smtplib.SMTPException as smtp_err:
        print(f"[EMAIL][ERROR] SMTP error: {smtp_err}")
        return False
    except Exception as e:
        # Log internally, but never leak details/credentials to the caller.
        print(f"[EMAIL][ERROR] Unexpected failure while sending to {to_email}: {e}")
        return False
