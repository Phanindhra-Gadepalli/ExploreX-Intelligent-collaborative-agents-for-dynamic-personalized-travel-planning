import os
from typing import Optional, Dict, Any, Union
import json
import re
import dotenv

dotenv.load_dotenv()

# The supported Google GenAI SDK (``from google import genai``).  The retired
# ``google.generativeai`` package must not be imported anywhere in this project.
_GENAI_CLIENTS: Dict[str, Any] = {}


def _get_genai_client():
    """Return a cached ``google.genai.Client`` or ``None`` when unconfigured.

    The key is read from the environment only (``GEMINI_API_KEY`` first, then
    ``GOOGLE_API_KEY``) and is never printed or embedded in an exception.
    """
    api_key = (os.getenv("GEMINI_API_KEY") or os.getenv("GOOGLE_API_KEY") or "").strip()
    if not api_key:
        print("[GENAI] No GEMINI_API_KEY/GOOGLE_API_KEY configured; skipping request.")
        return None
    client = _GENAI_CLIENTS.get(api_key)
    if client is None:
        from google import genai

        client = genai.Client(api_key=api_key)
        _GENAI_CLIENTS[api_key] = client
    return client


# Use Google Gemini to ask questions
def ask_openai(
    prompt: str,
    model: str = "gemini-flash-lite-latest",
    temperature: float = 0.7,
    max_tokens: int = 600  # Reduced for India-only scope — faster & cheaper LLM calls
) -> Optional[Dict[str, Any]]:
    """Compatibility wrapper that now calls the supported ``google-genai`` SDK.

    The name is historical (the app once used OpenAI); callers keep the same
    ``{"answer": ...}`` contract.  Prompts, model selection, temperature and
    output-token limits are unchanged.
    """
    try:
        client = _get_genai_client()
        if client is None:
            return None
        from google.genai import types

        response = client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(
                temperature=temperature,
                max_output_tokens=max_tokens,
            ),
        )
        answer = getattr(response, "text", None)
        if not answer:
            print("[GENAI] Empty response from model.")
            return None
        return {"answer": answer}
    except Exception as e:
        # Never echo the API key or the full request payload.
        print(f"[GENAI] ask_openai failed: {type(e).__name__}: {str(e)[:300]}")
        return None


def extract_number(text: str) -> Optional[float]:
    """
    Extract numbers from text, supporting multiple formats
    
    Args:
        text: Text containing numbers
    
    Returns:
        Extracted number (float), returns None if not found
    
    Examples:
        >>> extract_number("Price is $3.45 per gallon")
        3.45
        >>> extract_number("Gas price 4.25 dollars")
        4.25
        >>> extract_number("3,456.78")
        3456.78
    """
    try:
        # Remove all whitespace characters
        text = text.strip()
        
        # Try direct conversion to float
        try:
            return float(text)
        except ValueError:
            pass
        
        # Remove currency symbols and other non-numeric characters (keep numbers, decimal points and commas)
        text = re.sub(r'[^\d.,]', '', text)
        
        # Handle different decimal point formats
        if ',' in text and '.' in text:
            # If both comma and decimal point exist, assume comma is thousand separator
            text = text.replace(',', '')
        elif ',' in text:
            # If only comma exists, assume it's decimal point
            text = text.replace(',', '.')
        
        # Extract first number
        match = re.search(r'\d+\.?\d*', text)
        if match:
            return float(match.group())
            
        return None
        
    except Exception:
        return None

def extract_price(text: str, currency: str = "USD") -> Optional[float]:
    """
    Extract price from text, supporting multiple formats
    
    Args:
        text: Text containing price
        currency: Currency type (default USD)
    
    Returns:
        Extracted price (float), returns None if not found
    """
    try:
        text = text.strip()
        text = text.replace(currency, '').replace('$', '').replace('¥', '').replace('€', '')
        return extract_number(text)
    except Exception:
        return None
