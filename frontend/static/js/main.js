document.addEventListener('DOMContentLoaded', () => {
    // ── Single source of truth for the trip/session ────────────────────────
    // Every rendered surface reads from this object; nothing keeps a rival
    // copy. `resetTrip()` clears it, `restoreFromServer()` repopulates it.
    const state = {
        // session / ui
        sessionId: null,
        currentTab: 'chat',
        generating: false,
        // trip inputs (synced from the Explore controls + backend user_info)
        destination: null,
        origin: null,
        startDate: null,
        days: null,
        travelers: { people: null, kids: null },
        budgetLevel: null,
        interests: [],
        interestWeight: 80,
        email: '',
        // trip data
        attractions: [],
        selectedAttractionIds: new Set(),
        accommodations: [],
        selectedAccommodationId: null,
        food: [],
        transport: [],
        weather: [],
        itinerary: null,
        budget: null,
        // Selected attractions the planner could not schedule, with reasons.
        unscheduledAttractions: [],
        // map layers
        map: null,
        mapMarkers: [],
        routePolylines: [],
        mapCurrentDay: sessionStorage.getItem('mapCurrentDay') || 'all',
        activeItineraryDay: sessionStorage.getItem('activeItineraryDay') || 1,
        accTier: 'all',
        foodCat: 'all',
        selectedTransportIdx: null,
        // Transcript of the user's transport choice. Costs are NOT recomputed in
        // the browser - the server owns the single normalized breakdown - so a
        // change only flags the plan as needing a regenerate.
        selectedTransport: null,
        planIsStale: false
    };

    const navTabs = document.querySelectorAll('.nav-tab');
    const viewSections = document.querySelectorAll('.view-section');
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input-field');
    const chatContainer = document.getElementById('chat-container');
    const recContainer = document.getElementById('recommendations-container');
    const emptyStateHTML = document.getElementById('recommendations-empty')?.outerHTML || '';

    // === Toasts ===
    function showToast(message, type = 'info', timeout = 4200) {
        const c = document.getElementById('toast-container');
        if (!c) return;
        const icons = { success: 'fa-circle-check', error: 'fa-circle-exclamation', info: 'fa-circle-info' };
        const t = document.createElement('div');
        t.className = `ex-toast ${type}`;
        t.setAttribute('role', 'status');
        t.innerHTML = `<i class="fas ${icons[type] || icons.info} ex-toast-icon"></i><div>${escapeHtml(message)}</div>`;
        c.appendChild(t);
        setTimeout(() => { t.classList.add('hide'); setTimeout(() => t.remove(), 320); }, timeout);
    }
    window.showToast = showToast;

    // === Skeleton loaders for the recommendations grid ===
    function showRecommendationSkeletons(count = 6) {
        if (!recContainer) return;
        let html = '';
        for (let i = 0; i < count; i++) {
            html += `<div class="bento-card skeleton-card" aria-hidden="true">
                <div class="skeleton skeleton-img"></div>
                <div class="skeleton skeleton-line w-60"></div>
                <div class="skeleton skeleton-line w-40"></div>
                <div class="skeleton skeleton-line w-60"></div>
            </div>`;
        }
        recContainer.innerHTML = html;
    }
    function clearRecommendationSkeletons() {
        if (!recContainer) return;
        if (recContainer.querySelector('.skeleton-card')) recContainer.innerHTML = emptyStateHTML;
    }

    // === Navigation & Tabs ===
    navTabs.forEach(tab => tab.addEventListener('click', () => switchTab(tab.dataset.tab)));

    function switchTab(tabId) {
        navTabs.forEach(t => t.classList.remove('active'));
        document.querySelector(`.nav-tab[data-tab="${tabId}"]`)?.classList.add('active');

        viewSections.forEach(v => {
            v.classList.remove('active');
            if (v.id === `view-${tabId}`) v.classList.add('active');
        });
        state.currentTab = tabId;

        updateJourneyIndicator(tabId);

        if (tabId === 'map' && state.map) setTimeout(() => state.map.invalidateSize(), 100);
        // The assistant rail persists: keep it mounted and re-read the cached
        // server state instead of re-running any planning step.
        try { updateAssistantVisibility(); } catch (e) { }
        syncEarth();
    }

    function updateJourneyIndicator(tabId) {
        const bar = document.getElementById('journey-bar');
        if (!bar) return;
        
        if (tabId === 'landing') {
            bar.classList.remove('visible');
            return;
        }
        bar.classList.add('visible');
        
        const phases = ['chat', 'explore', 'map', 'itinerary'];
        let activeStep = tabId;
        if (tabId === 'weather' || tabId === 'budget') activeStep = 'itinerary';
        
        let reached = false;
        phases.forEach((step, idx) => {
            const el = document.getElementById(`step-${step}`);
            const conn = document.getElementById(`conn-${idx + 1}`);
            if (!el) return;
            
            el.classList.remove('active', 'done');
            if (conn) conn.classList.remove('done');
            
            if (step === activeStep) {
                el.classList.add('active');
                reached = true;
            } else if (!reached) {
                el.classList.add('done');
                if (conn) conn.classList.add('done');
            }
        });
    }

    // === Earth Animation (landing view only) ===
    let earthCtl = null;

    function initEarth(canvasId, containerId) {
        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);
        if (!canvas || !container || typeof THREE === 'undefined') return null;

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(38, container.clientWidth / container.clientHeight || 1, 0.1, 1000);
        const renderer = new THREE.WebGLRenderer({ canvas, alpha: true, antialias: true });
        renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
        renderer.setSize(container.clientWidth, container.clientHeight);

        const group = new THREE.Group();
        scene.add(group);

        const textureLoader = new THREE.TextureLoader();
        const earthTexture = textureLoader.load('https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg');
        const earth = new THREE.Mesh(
            new THREE.SphereGeometry(2.25, 64, 64),
            new THREE.MeshPhongMaterial({ map: earthTexture, shininess: 15, specular: new THREE.Color(0x333333) })
        );
        group.add(earth);

        const cloudTexture = textureLoader.load('https://threejs.org/examples/textures/planets/earth_clouds_1024.png');
        const clouds = new THREE.Mesh(
            new THREE.SphereGeometry(2.28, 64, 64),
            new THREE.MeshPhongMaterial({ map: cloudTexture, transparent: true, opacity: 0.4, depthWrite: false })
        );
        group.add(clouds);

        const atmosphere = new THREE.Mesh(
            new THREE.SphereGeometry(2.35, 64, 64),
            new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.1, side: THREE.BackSide })
        );
        group.add(atmosphere);

        scene.add(new THREE.AmbientLight(0xffffff, 1.05));
        const sun = new THREE.DirectionalLight(0xffffff, 1.7);
        sun.position.set(5, 3, 5);
        scene.add(sun);

        camera.position.z = 7.5;
        setTimeout(() => canvas.classList.add('visible'), 500);

        let raf = null;
        function frame() {
            raf = requestAnimationFrame(frame);
            earth.rotation.y += 0.002;
            clouds.rotation.y += 0.0025;
            renderer.render(scene, camera);
        }
        function start() { if (raf === null) frame(); }
        function stop() { if (raf !== null) { cancelAnimationFrame(raf); raf = null; } }

        window.addEventListener('resize', () => {
            if (container.clientWidth && container.clientHeight) {
                camera.aspect = container.clientWidth / container.clientHeight;
                camera.updateProjectionMatrix();
                renderer.setSize(container.clientWidth, container.clientHeight);
            }
        });

        return { start, stop };
    }

    // Only animate the earth while the landing view is actually visible.
    function syncEarth() {
        if (!earthCtl) return;
        const landing = document.getElementById('view-landing');
        const landingVisible = landing && landing.classList.contains('active');
        if (landingVisible) earthCtl.start();
        else earthCtl.stop();
    }

    // === Map ===
    function initMap() {
        if (!state.map && document.getElementById('map')) {
            state.map = L.map('map').setView([20.0, 0.0], 2);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap'
            }).addTo(state.map);
        }
    }

    // === Interests System (state is the source of truth) ===
    const interestWeight = document.getElementById('interest-weight');
    const weightVal = document.getElementById('weight-val');

    function readInterestsFromDom() {
        return Array.from(document.querySelectorAll('.interest-chip.selected'))
            .map(c => c.dataset.interest).filter(Boolean);
    }

    // Paint state.interests back onto the chips (used by restore + reset).
    function applyInterestsToDom() {
        document.querySelectorAll('.interest-chip').forEach(chip => {
            chip.classList.toggle('selected', state.interests.includes(chip.dataset.interest));
        });
        if (interestWeight) interestWeight.value = state.interestWeight;
        if (weightVal) weightVal.innerText = state.interestWeight + '%';
    }

    document.querySelectorAll('.interest-chip').forEach(chip => {
        chip.addEventListener('click', () => {
            chip.classList.toggle('selected');
            state.interests = readInterestsFromDom();
            fetchDestinations();
        });
    });

    if (interestWeight && weightVal) {
        interestWeight.addEventListener('input', e => {
            state.interestWeight = parseInt(e.target.value, 10) || 0;
            weightVal.innerText = state.interestWeight + '%';
        });
        interestWeight.addEventListener('change', e => {
            fetchDestinations();
        });
    }

    // Seed the model from the chips that are pre-selected in the markup.
    state.interests = readInterestsFromDom();
    state.interestWeight = interestWeight ? (parseInt(interestWeight.value, 10) || 80) : 80;

    function getSelectedInterestsString() {
        if (!state.interests.length) return '';
        return `${state.interests.join(', ')} (weight: ${state.interestWeight})`;
    }

    // === Auth-aware boot ===
    function currentUser() { return window.EXPLOREX || {}; }

    function showLanding() {
        document.getElementById('main-nav').style.display = 'none';
        viewSections.forEach(v => v.classList.remove('active'));
        document.getElementById('view-landing')?.classList.add('active');
        state.currentTab = 'landing';
        try { updateAssistantVisibility(); } catch (e) { }
        syncEarth();
    }

    function enterApp(name) {
        document.getElementById('main-nav').style.display = 'flex';
        const greeting = document.getElementById('chat-greeting');
        if (greeting && name) greeting.innerText = `Hello, ${name}!`;
        const navName = document.getElementById('nav-user-name');
        if (navName) navName.innerText = name || '';
        const avatar = document.getElementById('nav-user-avatar');
        if (avatar && name) avatar.src = `https://ui-avatars.com/api/?name=${encodeURIComponent(name)}&background=0284C7&color=fff`;
        switchTab('chat');
        initMap();
        if (chatContainer && chatContainer.children.length === 0) {
            addChatMessage(`Hi${name ? ' ' + name : ''}! I'm ExploreX. Tell me where you'd like to travel — for example: "Plan a 4-day trip to Agra, medium budget, I love history and temples, starting 2026-10-26."`, 'ai');
        }
    }

    function startTrip(destination) {
        enterApp(currentUser().userName);
        if (!destination) return;
        const interests = getSelectedInterestsString();
        const text = `I want to visit ${destination}. ${interests ? 'My interests are: ' + interests + '.' : ''}`.trim();
        addChatMessage(text, 'user');
        triggerBackend('chat', text);
    }

    window.startApp = startTrip;

    window.goToDestination = function (dest) {
        if (currentUser().authenticated) {
            startTrip(dest);
        } else {
            try { sessionStorage.setItem('explorex_dest', dest); } catch (e) { }
            window.location.href = '/login?dest=' + encodeURIComponent(dest);
        }
    };

    // ── Reset every trip surface (state model + DOM) ───────────────────────
    function resetTrip() {
        // map layers
        state.mapMarkers.forEach(m => state.map && state.map.removeLayer(m));
        state.routePolylines.forEach(p => state.map && state.map.removeLayer(p));
        state.mapMarkers = [];
        state.routePolylines = [];
        state.mapCurrentDay = 'all';
        state.unscheduledAttractions = [];
        assistantCache = null; assistantCacheAt = 0;
        try { sessionStorage.setItem('mapCurrentDay', 'all'); } catch (e) { }
        state.accTier = 'all';
        state.foodCat = 'all';
        state.selectedTransportIdx = null;
        state.selectedTransport = null;
        state.planIsStale = false;
        try { sessionStorage.removeItem('selectedTransportIdx'); sessionStorage.removeItem('selectedTransport'); } catch (e) { }

        // state model back to defaults (keep the Leaflet instance + tab)
        state.sessionId = null;
        state.generating = false;
        state.destination = null;
        state.origin = null;
        state.startDate = null;
        state.days = null;
        state.travelers = { people: null, kids: null };
        state.budgetLevel = null;
        state.interests = [];
        state.interestWeight = 80;
        state.email = '';
        state.attractions = [];
        state.selectedAttractionIds = new Set();
        state.accommodations = [];
        state.selectedAccommodationId = null;
        state.food = [];
        state.transport = [];
        state.weather = [];
        state.itinerary = null;
        state.budget = null;

        // DOM surfaces
        const rc = document.getElementById('recommendations-container');
        if (rc) rc.innerHTML = emptyStateHTML;
        const setHTML = (id, html) => { const el = document.getElementById(id); if (el) el.innerHTML = html; };
        setHTML('itinerary-timeline', '');
        setHTML('itinerary-day-pills', '');
        setHTML('map-timeline', '');
        setHTML('weather-container', '');
        setHTML('budget-breakdown', '');
        setHTML('food-options', '');
        setHTML('transport-options', '');
        setHTML('accommodation-options', '');
        setHTML('chat-container', '');
        const title = document.getElementById('itinerary-title'); if (title) title.innerText = 'Your Itinerary';
        const dist = document.getElementById('map-total-distance'); if (dist) dist.innerText = '—';
        const stops = document.getElementById('map-total-stops'); if (stops) stops.innerText = '—';
        const total = document.getElementById('total-budget-display'); if (total) total.innerText = '—';
        const donut = document.getElementById('budget-donut-chart'); if (donut) donut.style.background = '';
        const emailInput = document.getElementById('email-deliver-input'); if (emailInput) emailInput.value = '';
        const mapDetails = document.getElementById('map-location-details'); if (mapDetails) mapDetails.classList.add('d-none');
        const mapTl = document.getElementById('map-timeline'); if (mapTl) mapTl.classList.remove('d-none');

        applyInterestsToDom();
    }

    // ── Re-render every view from the state model ──────────────────────────
    function renderAll() {
        applyInterestsToDom();
        if (state.attractions.length) renderRecommendations(state.attractions);
        if (state.accommodations.length) renderAccommodations(state.accommodations);
        if (state.weather && (Array.isArray(state.weather) ? state.weather.length : Object.keys(state.weather).length)) renderWeather(state.weather);
        if (state.itinerary) {
            renderItinerary(state.itinerary);
            renderMap(state.itinerary);
            renderFood(state.food);
        }
        if (state.budget) renderBudget(state.budget);
        const t = state.transport;
        if (t && (Array.isArray(t) ? t.length : Object.keys(t).length)) renderTransport(t);
        // Trip data changed on the server side - let the assistant re-read it.
        try { refreshAssistant(true); } catch (e) { }
    }

    // ── Restore the current trip from the backend after a refresh ──────────
    async function restoreFromServer() {
        try {
            const r = await fetch('/api/state', { credentials: 'same-origin' });
            const data = await r.json();
            if (!data || data.status !== 'success' || !data.trip) return false;
            const t = data.trip;
            if (data.session_id) state.sessionId = data.session_id;
            state.destination = t.destination || null;
            state.origin = t.origin || null;
            state.startDate = t.start_date || null;
            state.days = t.days ?? null;
            state.budgetLevel = t.budget_level || null;
            state.travelers = { people: t.people ?? null, kids: t.kids ?? null };
            if (t.interests != null) {
                const list = Array.isArray(t.interests) ? t.interests : String(t.interests).split(',');
                const names = list.map(x => String(x).trim()).filter(Boolean);
                if (names.length) state.interests = names;
            }
            state.attractions = Array.isArray(t.attractions) ? t.attractions : [];
            state.accommodations = Array.isArray(t.accommodations) ? t.accommodations : [];
            const w = t.weather;
            state.weather = (w && (Array.isArray(w) ? w.length : Object.keys(w).length)) ? w : [];
            const itin = t.itinerary;
            state.itinerary = (Array.isArray(itin) ? itin.length : (itin && Array.isArray(itin.days) && itin.days.length)) ? itin : null;
            state.budget = (t.budget && Object.keys(t.budget).length) ? t.budget : null;
            // The server's canonical breakdown travels next to the raw budget so
            // the budget page and the email can never disagree about the total.
            if (state.budget && t.budget_normalized && Object.keys(t.budget_normalized).length) {
                state.budget.budget_normalized = t.budget_normalized;
            }
            state.unscheduledAttractions = Array.isArray(t.unscheduled_attractions) ? t.unscheduled_attractions : [];
            state.transport = t.transit_options || [];
            state.selectedAttractionIds = new Set(
                (Array.isArray(t.selected_attractions) ? t.selected_attractions : [])
                    .map(a => a && (a.id || a.attraction_id)).filter(Boolean)
            );
            renderAll();
            return !!(state.itinerary || state.attractions.length);
        } catch (e) {
            return false;
        }
    }

    document.getElementById('reset-btn')?.addEventListener('click', async () => {
        if (!confirm('Start a new trip? This clears all current progress.')) return;
        let newSessionId = null;
        try { 
            const resp = await fetch('/api/reset', { method: 'POST', credentials: 'same-origin' });
            const data = await resp.json();
            if (data.session_id) newSessionId = data.session_id;
        } catch (e) { }
        resetTrip();
        if (newSessionId) {
            state.sessionId = newSessionId;
        }
        
        // Ensure browser storage is cleared of trip-specific data
        try { sessionStorage.removeItem('explorex_dest'); } catch(e) {}
        try { localStorage.clear(); } catch(e) {}
        
        switchTab('chat');
        addChatMessage('Where would you like to travel today?', 'ai');
        showToast('Started a new trip. Previous data cleared.', 'success');
    });

    // === Logout ===
    document.getElementById('logout-btn')?.addEventListener('click', async () => {
        const btn = document.getElementById('logout-btn');
        if (btn) { btn.disabled = true; btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i>'; }
        try {
            await fetch('/api/auth/logout', { method: 'POST', credentials: 'same-origin' });
        } catch (e) {
        }
        try { sessionStorage.removeItem('explorex_dest'); } catch (e) { }
        window.location.href = '/login';
    });

    // === Chat ===
    chatForm?.addEventListener('submit', e => {
        e.preventDefault();
        let text = chatInput.value.trim();
        if (!text) return;

        addChatMessage(text, 'user');
        chatInput.value = '';

        if (!state.sessionId) {
            const interests = getSelectedInterestsString();
            if (interests && !text.toLowerCase().includes('interests are')) {
                text += ` (My interests are: ${interests})`;
            }
        }
        triggerBackend('chat', text);
    });

    function addChatMessage(text, sender) {
        if (!chatContainer) return null;
        const div = document.createElement('div');
        div.className = sender === 'user' ? 'user-msg' : 'ai-msg';
        const icon = sender === 'user' ? '<i class="fas fa-user"></i>' : '<i class="fas fa-robot"></i>';
        div.innerHTML = `
            ${sender === 'ai' ? `<div class="avatar">${icon}</div>` : ''}
            <div class="msg-content">${text}</div>
            ${sender === 'user' ? `<div class="avatar bg-primary text-white border-0">${icon}</div>` : ''}
        `;
        chatContainer.appendChild(div);
        chatContainer.scrollTop = chatContainer.scrollHeight;
        return div.querySelector('.msg-content');
    }

    // === Backend Communication (SSE) — single, event-driven driver ===
    // The backend already auto-loops chat -> information -> retrieval -> recommend
    // and recommend -> strategy -> communication. We only drive the two pauses
    // that require real user intent: (1) recommend (attraction selection) and
    // (2) communication -> route (explicit confirmation to build the itinerary).

    // ── Phase 5 helpers: agent trace, RAG sources, chat actions ────────────
    function bubbleOf(msgDiv) { return msgDiv ? msgDiv.closest('.ai-msg') : null; }

    function ensureTraceEl(bubble) {
        if (!bubble) return null;
        let el = bubble.querySelector('.agent-trace');
        if (!el) { el = document.createElement('div'); el.className = 'agent-trace'; bubble.appendChild(el); }
        return el;
    }

    // `trace` is the real list of agent labels emitted by the backend loop.
    function renderTrace(bubble, trace, live) {
        const el = ensureTraceEl(bubble);
        if (!el) return;
        if (!trace || !trace.length) { el.innerHTML = ''; return; }
        const items = trace.map((a, i) => {
            const isLast = i === trace.length - 1;
            const icon = (isLast && live) ? 'fa-spinner fa-spin' : 'fa-circle-check';
            return `<span class="trace-step${(isLast && live) ? ' active' : ''}"><i class="fas ${icon}"></i> ${escapeHtml(a)}</span>`;
        }).join('');
        el.innerHTML = `<span class="trace-label"><i class="fas fa-diagram-project"></i> Agents</span>${items}`;
    }

    function sourceChipsHTML(sources) {
        if (!Array.isArray(sources) || !sources.length) return '';
        const chips = sources.map(s => {
            const name = escapeHtml(s.name || 'Source');
            const icon = s.type === 'web' ? 'fa-globe' : 'fa-book';
            return s.url
                ? `<a class="rag-chip" href="${escapeHtml(s.url)}" target="_blank" rel="noopener"><i class="fas ${icon}"></i> ${name}</a>`
                : `<span class="rag-chip"><i class="fas ${icon}"></i> ${name}</span>`;
        }).join('');
        return `<div class="rag-sources-inline"><span class="rag-label"><i class="fas fa-quote-left"></i> Sources</span>${chips}</div>`;
    }

    function renderRagBar(sources) {
        const bar = document.getElementById('rag-sources');
        if (!bar) return;
        if (!Array.isArray(sources) || !sources.length) {
            bar.classList.add('d-none');
            bar.innerHTML = '<span>Sources:</span>';
            return;
        }
        bar.classList.remove('d-none');
        bar.innerHTML = '<span>Sources:</span>' + sources.map(s => {
            const name = escapeHtml(s.name || 'Source');
            return s.url ? `<a href="${escapeHtml(s.url)}" target="_blank" rel="noopener">${name}</a>` : `<span>${name}</span>`;
        }).join('');
    }

    // Advance the real recommend -> strategy -> communication -> route flow.
    function generateItineraryFromChat(data) {
        if (state.generating) { showToast('Already planning your itinerary…', 'info'); return; }
        const recs = (data && data.recommended_attractions) || state.attractions || [];
        if (state.selectedAttractionIds.size === 0) {
            recs.forEach(a => { const id = a && (a.id || a.attraction_id); if (id) state.selectedAttractionIds.add(id); });
        }
        if (state.selectedAttractionIds.size === 0) {
            showToast('Select at least one attraction first.', 'error');
            switchTab('explore');
            return;
        }
        const ids = Array.from(state.selectedAttractionIds);
        const params = { selected_attraction_ids: JSON.stringify(ids), force_continue: 'true' };
        if (state.selectedAccommodationId) params.selected_accommodation_id = state.selectedAccommodationId;
        state.generating = true;
        switchTab('chat');
        triggerBackend('recommend', 'Here are my selected attractions', params);
    }

    // Contextual action buttons on an AI message. Every button mutates real state.
    function addChatActions(bubble, data) {
        if (!bubble) return;
        bubble.querySelectorAll('.msg-actions').forEach(e => e.remove());
        const hasRecs = Array.isArray(data.recommended_attractions) && data.recommended_attractions.length;
        const hasItin = !!data.itinerary;
        if (!hasRecs && !hasItin) return;

        const wrap = document.createElement('div');
        wrap.className = 'msg-actions';
        const mk = (label, icon, fn) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.className = 'btn btn-sm btn-outline rounded-pill';
            b.innerHTML = `<i class="fas ${icon} me-1"></i>${label}`;
            b.addEventListener('click', fn);
            return b;
        };

        if (hasRecs) {
            wrap.appendChild(mk('View on Map', 'fa-map', () => {
                switchTab('map');
                if (state.map && state.itinerary) renderMap(state.itinerary);
                showToast('Showing your trip on the map.', 'info');
            }));
            wrap.appendChild(mk('Save to Itinerary', 'fa-bookmark', () => {
                const recs = data.recommended_attractions || [];
                recs.forEach(a => { const id = a && (a.id || a.attraction_id); if (id) state.selectedAttractionIds.add(id); });
                renderRecommendations(state.attractions.length ? state.attractions : recs);
                showToast(`${state.selectedAttractionIds.size} place(s) saved to your itinerary selection.`, 'success');
            }));
        }
        if (hasItin) {
            wrap.appendChild(mk('View on Map', 'fa-map', () => { switchTab('map'); renderMap(state.itinerary); }));
            wrap.appendChild(mk('View Itinerary', 'fa-calendar-alt', () => switchTab('itinerary')));
        }
        wrap.appendChild(mk('Modify Plan', 'fa-pen', () => {
            switchTab('chat');
            if (chatInput) { chatInput.value = "I'd like to modify the plan: "; chatInput.focus(); }
        }));
        if (hasRecs) {
            wrap.appendChild(mk('Generate Itinerary', 'fa-magic', () => generateItineraryFromChat(data)));
        }
        bubble.appendChild(wrap);
    }

    function triggerBackend(step, userInput, extraParams = {}) {
        let url = `/api/stream?step=${step}&user_input=${encodeURIComponent(userInput)}`;
        if (state.sessionId) url += `&session_id=${state.sessionId}`;
        for (const [k, v] of Object.entries(extraParams)) url += `&${k}=${encodeURIComponent(v)}`;

        const msgDiv = addChatMessage('', 'ai');
        const bubble = bubbleOf(msgDiv);
        if (msgDiv) msgDiv.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Processing...';

        // Give the Explore view instant feedback while a trip is being planned.
        if (step === 'chat') showRecommendationSkeletons();

        const eventSource = new EventSource(url);
        let fullText = '';
        const agentTrace = [];

        // Watchdog: guarantees the spinner can never run forever. Re-armed on
        // every event; fires only if the stream goes completely idle. Some real
        // steps (POI/Overpass + weather fetch, route optimisation) can run for
        // minutes without emitting, so the idle window is generous.
        let idleTimer = null;
        const IDLE_MS = 240000;
        const stopWatchdog = () => { if (idleTimer) { clearTimeout(idleTimer); idleTimer = null; } };
        const armWatchdog = () => {
            stopWatchdog();
            idleTimer = setTimeout(() => {
                eventSource.close();
                state.generating = false;
                clearRecommendationSkeletons();
                renderTrace(bubble, agentTrace, false);
                if (msgDiv && !fullText) {
                    msgDiv.innerHTML = '<span class="text-danger"><i class="fas fa-triangle-exclamation"></i> The request took too long and was stopped. Please try again.</span>';
                }
                showToast('The assistant timed out. Please try again.', 'error');
            }, IDLE_MS);
        };
        armWatchdog();

        eventSource.onmessage = e => {
            let data;
            try { data = JSON.parse(e.data); } catch (err) { return; }
            armWatchdog();

            // Real per-step agent trace emitted by the backend loop.
            if (data.type === 'status') {
                if (data.agent && agentTrace[agentTrace.length - 1] !== data.agent) agentTrace.push(data.agent);
                renderTrace(bubble, agentTrace, true);
                
                let uiMsg = `Working (${escapeHtml(data.agent || 'Working')})...`;
                if (data.agent === 'information') {
                    uiMsg = "Analyzing your request and fetching destination information...";
                } else if (data.agent === 'recommend') {
                    uiMsg = "Finding personalized attractions...";
                    // User must not stare at Chat. Automatically open explore while loading.
                    switchTab('explore');
                    showRecommendationSkeletons();
                } else if (data.agent === 'strategy') {
                    uiMsg = "Generating optimal daily strategy...";
                    switchTab('map'); // Switch to Plan/route progress
                } else if (data.agent === 'budget') {
                    uiMsg = "Calculating budget and transport costs...";
                    switchTab('map');
                } else if (data.agent === 'route') {
                    uiMsg = "Planning route and assigning times...";
                    switchTab('map');
                } else if (data.agent === 'communication') {
                    uiMsg = "Finalizing itinerary details...";
                    switchTab('itinerary');
                }
                
                if (msgDiv && !fullText) msgDiv.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i> ${uiMsg}`;
                return;
            }

            if (data.type === 'error') {
                stopWatchdog();
                let humanError = data.error || 'Something went wrong.';
                if (humanError.includes('AttributeError') || humanError.includes('NoneType') || humanError.includes('JSON decode error') || humanError.includes('stack trace')) {
                    humanError = "Your itinerary could not be completed because an unexpected error occurred. We've skipped that segment and are continuing.";
                }
                if (msgDiv) msgDiv.innerHTML = `<div class="p-3 bg-danger text-white rounded mb-2"><i class="fas fa-exclamation-triangle me-2"></i> ${escapeHtml(humanError)}</div>`;
                renderTrace(bubble, agentTrace, false);
                showToast(humanError, 'error');
                clearRecommendationSkeletons();
                state.generating = false;
                eventSource.close();
                return;
            }

            if (data.type === 'chunk') {
                if (fullText === '' && msgDiv) msgDiv.innerHTML = '';
                fullText += data.content;
                if (msgDiv) msgDiv.innerHTML = marked.parse(fullText);
                if (chatContainer) chatContainer.scrollTop = chatContainer.scrollHeight;
                return;
            }

            // Truthful keep-alive from the backend while a long step genuinely
            // runs. Shows real elapsed seconds — no fabricated progress bars.
            if (data.type === 'progress') {
                if (msgDiv && !fullText) {
                    msgDiv.innerHTML = `<i class="fas fa-circle-notch fa-spin"></i> ${escapeHtml(data.message || 'Working...')}`;
                }
                return;
            }

            if (data.type !== 'complete') return;

            stopWatchdog();
            eventSource.close();
            
            // Validate that the response belongs to the current session to prevent race conditions
            if (data.session_id) {
                if (state.sessionId && state.sessionId !== data.session_id) {
                    console.warn(`[ExploreX] Ignoring response for stale session ${data.session_id}`);
                    return;
                }
                state.sessionId = data.session_id;
            }
            
            if (msgDiv && fullText.trim() === '') msgDiv.innerHTML = 'Got it! Preparing the next steps.';
            renderTrace(bubble, agentTrace, false);

            // Real RAG/web provenance — only rendered when sources actually exist.
            if (Array.isArray(data.rag_sources) && data.rag_sources.length) {
                if (bubble) bubble.insertAdjacentHTML('beforeend', sourceChipsHTML(data.rag_sources));
                renderRagBar(data.rag_sources);
            }

            handleComplete(data);
            addChatActions(bubble, data);
        };

        eventSource.onerror = () => {
            stopWatchdog();
            eventSource.close();
            state.generating = false;
            clearRecommendationSkeletons();
            renderTrace(bubble, agentTrace, false);
            if (msgDiv && !fullText) msgDiv.innerHTML = `<span class="text-danger">Connection lost. Please try again.</span>`;
        };
    }

    // Pull trip metadata out of the backend state into the single model.
    function absorbServerState(s) {
        if (!s) return;
        const info = s.user_info || {};
        if (info.city != null) state.destination = info.city;
        if (info.origin_city != null) state.origin = info.origin_city;
        if (info.start_date != null) state.startDate = info.start_date;
        if (info.days != null) state.days = info.days;
        if (info.budget != null) state.budgetLevel = info.budget;
        if (info.people != null) state.travelers.people = info.people;
        if (info.kids != null) state.travelers.kids = info.kids;
        if (info.email && !state.email) state.email = info.email;
        if (info.hobbies != null) {
            const h = info.hobbies;
            const list = Array.isArray(h) ? h : String(h).split(',');
            const names = list.map(x => String(x).trim()).filter(Boolean);
            if (names.length) state.interests = names;
        }
    }

    function handleComplete(data) {
        const ns = data.next_step;

        // Capture weather/accommodation/user_info from state whenever any step
        // provides it (the final route step does not return `state`, but
        // communication does).
        if (data.state) {
            absorbServerState(data.state);
            if (data.state.weather_forecast) {
                state.weather = data.state.weather_forecast;
                renderWeather(data.state.weather_forecast);
            }
            if (Array.isArray(data.state.accommodations) && data.state.accommodations.length && !state.accommodations.length) {
                state.accommodations = data.state.accommodations;
                renderAccommodations(state.accommodations);
            }
            if (Array.isArray(data.state.restaurants) && data.state.restaurants.length) {
                state.food = data.state.restaurants;
            }
        }

        // Recommendations ready -> render + let the user choose.
        if (data.ui_action === 'OPEN_EXPLORE' || ns === 'recommend' || (Array.isArray(data.recommended_attractions) && data.recommended_attractions.length)) {
            state.attractions = data.recommended_attractions || state.attractions || [];
            if (state.attractions.length) {
                renderRecommendations(state.attractions);
            }
            if (Array.isArray(data.accommodations)) {
                state.accommodations = data.accommodations;
                renderAccommodations(state.accommodations);
            }
            switchTab('explore');
            const count = state.attractions.length;
            showToast(`${count} recommendations found. Select the places you'd like to visit.`, 'success');
        } else if (['chat', 'information', 'retrieval'].includes(ns)) {
            // Still conversing / gathering info — drop the placeholder skeletons.
            clearRecommendationSkeletons();
        }

        // Selection validation warning from the recommend step.
        if (data.validation_warning) {
            addChatMessage(
                `You selected ${data.selected_count} attraction(s); a ${data.required_count || '?'}-minimum is recommended. ` +
                `Select a few more, or click "Generate Itinerary" again to continue anyway.`, 'ai');
            showToast('Select a few more attractions for a fuller plan.', 'info');
            state.generating = false;
            return;
        }

        // Strategy pauses after showing the AI plan/car-rental recommendation.
        // A second strategy call advances to communication (backend auto-loops).
        if (ns === 'strategy') {
            triggerBackend('strategy', 'plan the route');
            return;
        }

        // Communication tips shown -> confirm to build the route/itinerary.
        if (ns === 'communication') {
            triggerBackend('communication', 'proceed');
            return;
        }

        // Final itinerary / budget / route.
        if (data.unscheduled_attractions || (data.state && data.state.unscheduled_attractions)) {
            state.unscheduledAttractions = data.unscheduled_attractions
                || (data.state && data.state.unscheduled_attractions) || [];
        }
        if (data.itinerary) {
            state.itinerary = data.itinerary;
            renderItinerary(data.itinerary);
            renderMap(data.itinerary);
            renderFood(state.food);
        }
        if (data.budget) {
            state.budget = data.budget;
            const nb = data.budget_normalized || (data.state && data.state.budget_normalized);
            if (nb && Object.keys(nb).length) state.budget.budget_normalized = nb;
            renderBudget(state.budget);
        }
        if (data.state && data.state.weather_forecast) {
            state.weather = data.state.weather_forecast;
            renderWeather(data.state.weather_forecast);
        }
        if (data.transit_options) { state.transport = data.transit_options; renderTransport(data.transit_options); }

        if (ns === 'route') {
            switchTab('map');
        }

        if (ns === 'itinerary' || data.itinerary) {
            state.generating = false;
            switchTab('itinerary');
            showToast('Your itinerary, budget and route are ready.', 'success');
        }
        // Whatever step just finished, the assistant rail re-reads the persisted
        // state - it does not re-run any planning agent.
        try { refreshAssistant(true); } catch (e) { }
    }

    // === Render Logic ===
    function fieldScore(a) {
        const raw = a.score ?? a.match_percentage ?? a._internal_score ?? a.match_score;
        const n = typeof raw === 'number' ? raw : parseFloat(raw);
        if (isNaN(n)) return null;               // unknown - rendered as "Not scored"
        return n > 1 ? Math.round(n) : Math.round(n * 100);
    }
    function fieldDuration(a) { return a.duration || a.estimated_duration || a.visit_duration || '2h'; }
    function fieldAddress(a) { return a.address || a.location_name || a.category || 'Location'; }
    // No invented scores: when neither the provider nor the enrichment service
    // reports a rating, the UI says so instead of quietly showing a 4.5.
    function fieldRating(a) {
        const raw = a.rating ?? a.stars;
        const n = typeof raw === 'number' ? raw : parseFloat(raw);
        return (isNaN(n) || n <= 0) ? null : n;
    }
    function fieldFee(a) {
        const fee = a.entry_fee ?? a.price;
        if (fee === null || fee === undefined || fee === '' || fee === true) return 'Fee not reported';
        if (typeof fee === 'string' && /^(yes|no)$/i.test(fee.trim())) {
            return fee.trim().toLowerCase() === 'no' ? 'Free (per map data)' : 'Paid (amount unknown)';
        }
        return fee;
    }
    function fieldImage(a) {
        const img = a.image_url || a.image || a.photo;
        if (img && String(img).startsWith('http')) return img;
        
        const hash = a.name ? a.name.charCodeAt(0) % 6 : 0;
        const fallbacks = [
            "https://images.unsplash.com/photo-1502602898657-3e9076113801",
            "https://images.unsplash.com/photo-1564507592333-c60657eea523",
            "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf",
            "https://images.unsplash.com/photo-1537996194471-e657df975ab4",
            "https://images.unsplash.com/photo-1512453979798-5ea266f8880c",
            "https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9"
        ];
        return fallbacks[hash] + '?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80';
    }
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function renderRecommendations(attrs) {
        const recContainer = document.getElementById('recommendations-container');
        const destContainer = document.getElementById('destinations-container');
        if (!recContainer) return;

        if (attrs && attrs.length > 0) {
            recContainer.style.display = 'grid';
            if (destContainer) destContainer.style.display = 'none';
        } else {
            // Show empty state if no attrs and no destinations
            if (recContainer.querySelector('.empty-state')) {
                recContainer.style.display = 'block';
            }
        }

        recContainer.innerHTML = '';
        if (!attrs || attrs.length === 0) {
            recContainer.innerHTML = emptyStateHTML;
            return;
        }

        attrs.forEach((a, idx) => {
            if (!a) return;
            const id = a.id || `attr-${idx}`;
            const div = document.createElement('div');
            div.className = 'bento-card';
            const testIdName = a.name ? a.name.toLowerCase().replace(/[^a-z0-9]+/g, '-') : 'attraction';
            div.setAttribute('data-testid', `attraction-card-${testIdName}`);

            const selected = state.selectedAttractionIds.has(id);
            const hash = a.name ? a.name.charCodeAt(0) % 6 : 0;
            const fallbacks = [
                "https://images.unsplash.com/photo-1502602898657-3e9076113801",
                "https://images.unsplash.com/photo-1564507592333-c60657eea523",
                "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf",
                "https://images.unsplash.com/photo-1537996194471-e657df975ab4",
                "https://images.unsplash.com/photo-1512453979798-5ea266f8880c",
                "https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9"
            ];
            const fallbackUrl = escapeHtml(fallbacks[hash] + '?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80');
            const primaryImg = escapeHtml(fieldImage(a));
            
            div.innerHTML = `
                <div class="skeleton-img-loader" style="width:100%; height:180px; background:var(--bg-alt); position:relative;">
                    <img src="${primaryImg}" class="bento-img" style="position:absolute; top:0; left:0; width:100%; height:100%; object-fit:cover;"
                        onload="this.parentElement.style.background='none';"
                        onerror="this.src='${fallbackUrl}'; this.onerror=null;">
                </div>
                <div class="bento-content">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <h5 class="Bricolage mb-0">${escapeHtml(a.name || 'Attraction')}</h5>
                        <div class="match-badge"><i class="fas fa-check-circle"></i> ${fieldScore(a) === null ? 'Not scored' : `${fieldScore(a)}% Match`}</div>
                    </div>
                    <p class="text-muted small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(fieldAddress(a))}</p>
                    <div class="d-flex justify-content-between text-secondary small mb-2">
                        <span><i class="fas fa-star text-warning me-1"></i> ${fieldRating(a) === null ? 'Not rated' : fieldRating(a)}</span>
                        <span><i class="fas fa-clock me-1"></i> ${escapeHtml(fieldDuration(a))}</span>
                    </div>
                    <div class="d-flex justify-content-between text-secondary small mb-2">
                        <span><i class="fas fa-ticket-alt text-info me-1"></i> ${escapeHtml(fieldFee(a))}</span>
                        <span><i class="fas fa-headphones text-primary me-1"></i> ${a.audio_guide || a.metadata?.audio_guide ? 'Audio Guide' : 'No Audio'}</span>
                    </div>
                    <p class="text-muted small mb-0">${escapeHtml(a.description || 'No description provided by the data source.').substring(0, 80)}${(a.description || 'No description provided by the data source.').length > 80 ? '...' : ''}</p>
                </div>
                <div class="p-3 border-top d-flex gap-2 align-items-center" style="background: var(--bg-alt); font-size: 0.9rem;">
                    <button class="btn btn-sm btn-outline-secondary w-50 view-map-btn" data-testid="attr-map-btn"><i class="fas fa-map-marker-alt me-1"></i> Map</button>
                    <button class="btn btn-sm ${selected ? 'btn-success' : 'btn-outline-primary'} toggle-select-btn w-50" data-testid="attr-select-btn" data-id="${escapeHtml(id)}">
                        ${selected ? '<i class="fas fa-check"></i> Added' : '<i class="fas fa-plus"></i> Add'}
                    </button>
                </div>
            `;
            recContainer.appendChild(div);

            div.querySelector('.bento-content')?.addEventListener('click', () => openAttractionModal(a));
            div.querySelector('.bento-img')?.addEventListener('click', () => openAttractionModal(a));

            div.querySelector('.view-map-btn')?.addEventListener('click', e => { 
                e.stopPropagation(); 
                switchTab('map');
                if (!state.map) initMap();
                if (state.map) {
                    const lat = a.latitude || (a.location && a.location.lat) || (a.location && a.location.latitude);
                    const lng = a.longitude || (a.location && a.location.lng) || (a.location && a.location.longitude);
                    if (lat && lng) {
                        state.map.setView([lat, lng], 15);
                        L.marker([lat, lng]).addTo(state.map)
                            .bindPopup(`<b>${escapeHtml(a.name)}</b><br>${escapeHtml(fieldAddress(a))}`)
                            .openPopup();
                    } else {
                        showToast('Location coordinates not available for this attraction.', 'error');
                    }
                }
            });

            const toggleBtn = div.querySelector('.toggle-select-btn');
            toggleBtn.addEventListener('click', e => { e.stopPropagation(); toggleSelect(a, id, toggleBtn); });
        });
    }

    let fetchDestinationsTimeout = null;
    async function fetchDestinations() {
        // If we already have attractions, we don't show destinations.
        if (state.attractions && state.attractions.length > 0) return;
        
        if (fetchDestinationsTimeout) {
            clearTimeout(fetchDestinationsTimeout);
        }
        
        fetchDestinationsTimeout = setTimeout(async () => {
            try {
                const res = await fetch('/api/destinations', {
                    method: 'POST',
                    headers: { 'Content-Type': 'application/json' },
                    body: JSON.stringify({ interests: state.interests, weight: state.interestWeight })
                });
                const data = await res.json();
                if (data.status === 'success') {
                    renderDestinations(data.destinations);
                }
            } catch (e) {
                console.error("Error fetching destinations:", e);
            }
        }, 300);
    }

    function renderDestinations(dests) {
        const destContainer = document.getElementById('destinations-container');
        const recContainer = document.getElementById('recommendations-container');
        if (!destContainer) return;
        
        destContainer.innerHTML = '';
        if (dests && dests.length > 0) {
            destContainer.style.display = 'grid';
            if (recContainer) recContainer.style.display = 'none';
            
            dests.forEach(d => {
                const div = document.createElement('div');
                div.className = 'bento-card';
                div.innerHTML = `
                    <img src="${escapeHtml(d.image)}" class="bento-img" onerror="this.style.display='none'">
                    <div class="bento-content">
                        <div class="d-flex justify-content-between align-items-start mb-2">
                            <h5 class="Bricolage mb-0">${escapeHtml(d.name)}, ${escapeHtml(d.country)}</h5>
                            <div class="match-badge"><i class="fas fa-check-circle"></i> ${escapeHtml(d.match_percentage)}% Match</div>
                        </div>
                        <p class="text-muted small mb-3"><i class="fas fa-tag text-accent me-1"></i> ${escapeHtml(d.tag)}</p>
                        <div class="d-flex justify-content-between text-secondary small">
                            <span><i class="fas fa-star text-warning me-1"></i> ${escapeHtml(d.rating)}</span>
                            <span><i class="fas fa-wallet me-1"></i> ${escapeHtml(d.estimated_cost)}</span>
                        </div>
                        <div class="mt-2 text-secondary small">
                            <i class="fas fa-cloud-sun me-1"></i> ${escapeHtml(d.weather_snippet)}
                        </div>
                    </div>
                    <div class="p-3 border-top d-flex justify-content-between align-items-center" style="background: var(--bg-alt); font-size: 0.9rem;">
                        <button class="btn btn-primary w-100 btn-explore-route" data-testid="btn-explore-route">Explore Route <i class="fas fa-arrow-right ms-2"></i></button>
                    </div>
                `;
                destContainer.appendChild(div);

                div.querySelector('.btn-explore-route')?.addEventListener('click', () => {
                    // Pre-fill chat with the destination request!
                    switchTab('chat');
                    const interests = getSelectedInterestsString();
                    const text = `I want to visit ${d.name}. ${interests ? 'My interests are: ' + interests + '.' : ''}`.trim();
                    const chatInput = document.getElementById('chat-input-field');
                    if (chatInput) {
                        chatInput.value = text;
                        document.getElementById('chat-send-button')?.click();
                    }
                });
            });
        }
    }

    function toggleSelect(a, id, btn) {
        if (state.selectedAttractionIds.has(id)) {
            state.selectedAttractionIds.delete(id);
            if (btn) { btn.classList.remove('btn-success'); btn.classList.add('btn-outline-primary'); btn.innerHTML = 'Select'; }
        } else {
            state.selectedAttractionIds.add(id);
            if (btn) { btn.classList.add('btn-success'); btn.classList.remove('btn-outline-primary'); btn.innerHTML = '<i class="fas fa-check"></i> Selected'; }
        }
    }

    let currentAttractionModal = null;

    /**
     * Requirement 2: query /api/attraction-details (Wikipedia-backed) and render
     * only fields the backend actually returned. Renders a Wikipedia search
     * fallback link when no verified article exists — never invents a title.
     */
    async function _fetchAttractionDetails(a, slots, lat, lng) {
        const { enrichWrap, sourceBadge, hoursWrap, hoursVal, linksWrap } = slots;
        if (!a || !a.name) { if (enrichWrap) enrichWrap.classList.add('d-none'); return; }
        const url = new URL('/api/attraction-details', window.location.origin);
        url.searchParams.set('name', a.name);
        if (lat != null) url.searchParams.set('lat', String(lat));
        if (lng != null) url.searchParams.set('lng', String(lng));
        let data = null;
        try {
            const resp = await fetch(url.toString(), { credentials: 'same-origin' });
            if (resp.ok) data = await resp.json();
        } catch (e) {
            console.warn('attraction-details fetch failed', e);
        }
        // Guard against a late response arriving after the user switched POIs.
        if (enrichWrap && enrichWrap.dataset.attractionId && a.id && enrichWrap.dataset.attractionId !== a.id) return;

        const merged = data || {};
        const source = String(merged.source || a.source || '').toLowerCase();
        const bits = [];
        if (merged.description_source === 'wikipedia') bits.push('<i class="fab fa-wikipedia-w me-1"></i> Wikipedia (verified)');
        else if (source) bits.push('Source: ' + escapeHtml(source));
        if (merged.opening_hours) {
            bits.push('OSM opening hours');
            if (hoursVal) { hoursVal.textContent = String(merged.opening_hours); }
            if (hoursWrap) hoursWrap.classList.remove('d-none');
        }
        if (sourceBadge) sourceBadge.innerHTML = bits.join(' · ') || '';

        const linkBits = [];
        const wikiUrl = merged.wiki_url || merged.wikipedia_url || null;
        const officialSite = merged.website || null;
        if (wikiUrl) {
            linkBits.push(`<a class="btn btn-sm btn-outline-primary rounded-pill" target="_blank" rel="noopener noreferrer" href="${encodeURI(wikiUrl)}"><i class="fab fa-wikipedia-w me-1"></i> Wikipedia article</a>`);
        } else {
            // Not fabricated: a plain Wikipedia *search* link built from the POI name.
            const searchQ = encodeURIComponent(a.name + (a.city ? ' ' + a.city : (merged.address ? ' ' + merged.address : '')));
            linkBits.push(`<span class="text-muted small me-2"><i class="fab fa-wikipedia-w me-1"></i> Wikipedia article unavailable</span>`);
            linkBits.push(`<a class="btn btn-sm btn-outline-secondary rounded-pill" target="_blank" rel="noopener noreferrer" href="https://en.wikipedia.org/w/index.php?search=${searchQ}"><i class="fas fa-search me-1"></i> Search Wikipedia</a>`);
        }
        if (officialSite) {
            linkBits.push(`<a class="btn btn-sm btn-outline-success rounded-pill" target="_blank" rel="noopener noreferrer" href="${encodeURI(officialSite)}"><i class="fas fa-globe me-1"></i> Official website</a>`);
        }
        if (linksWrap) linksWrap.innerHTML = linkBits.join(' ');

        // If Wikipedia returned a richer description than what we already had,
        // swap it in — but only when the backend flagged it as sourced.
        if (merged.description && merged.description_source === 'wikipedia') {
            const descEl = document.getElementById('modal-desc');
            if (descEl) descEl.textContent = merged.description;
        }
    }
    
    function closeAttractionModal() {
        if (currentAttractionModal) {
            currentAttractionModal.hide();
            // currentAttractionModal = null; // Do not nullify, bootstrap handles the instance
        }
    }

    // Global ESC handler
    document.addEventListener('keydown', (e) => {
        if (e.key === 'Escape' && document.getElementById('attractionModal').classList.contains('show')) {
            closeAttractionModal();
        }
    });

    function openAttractionModal(a) {
        const id = a.id || '';
        document.getElementById('modal-title').innerText = a.name || 'Attraction';
        document.getElementById('modal-location').innerText = fieldAddress(a);
        const modalRatingEl = document.getElementById('modal-rating');
        if (modalRatingEl) {
            const rv = fieldRating(a);
            modalRatingEl.innerText = rv === null ? 'Not rated' : String(rv);
        }
        document.getElementById('modal-desc').innerText = a.description || 'No description available.';

        // Requirement 2: reset the enrichment panel and (asynchronously) fetch
        // verified details from /api/attraction-details. Any field we cannot
        // validate is hidden, never fabricated. The user can still read the
        // base description while the enrichment streams in.
        const enrichWrap = document.getElementById('modal-enrichment');
        const sourceBadge = document.getElementById('modal-source-badge');
        const hoursWrap = document.getElementById('modal-hours');
        const hoursVal  = document.getElementById('modal-hours-value');
        const linksWrap = document.getElementById('modal-links');
        if (enrichWrap) { enrichWrap.classList.remove('d-none'); enrichWrap.dataset.attractionId = id || ''; }
        if (sourceBadge) sourceBadge.innerHTML = '<i class="fas fa-circle-notch fa-spin me-1"></i> Looking up verified details…';
        if (hoursWrap) hoursWrap.classList.add('d-none');
        if (linksWrap) linksWrap.innerHTML = '';
        const latForLookup = a.latitude || (a.location && a.location.lat) || (a.location && a.location.latitude) || null;
        const lngForLookup = a.longitude || (a.location && a.location.lng) || (a.location && a.location.longitude) || null;
        _fetchAttractionDetails(a, { enrichWrap, sourceBadge, hoursWrap, hoursVal, linksWrap }, latForLookup, lngForLookup);

        const durationEl = document.getElementById('modal-duration');
        if (durationEl) durationEl.innerText = fieldDuration(a);
        const feeEl = document.getElementById('modal-fee');
        if (feeEl) {
            // Only call something "Free" when a source actually says so.
            feeEl.innerText = String(fieldFee(a));
        }

        const img = document.getElementById('modal-image');
        img.style.display = 'block';
        img.src = fieldImage(a);
        img.onerror = function() {
            const hash = a.name ? a.name.charCodeAt(0) % 6 : 0;
            const fallbacks = [
                "https://images.unsplash.com/photo-1502602898657-3e9076113801",
                "https://images.unsplash.com/photo-1564507592333-c60657eea523",
                "https://images.unsplash.com/photo-1540959733332-eab4deabeeaf",
                "https://images.unsplash.com/photo-1537996194471-e657df975ab4",
                "https://images.unsplash.com/photo-1512453979798-5ea266f8880c",
                "https://images.unsplash.com/photo-1496442226666-8d4d0e62e6e9"
            ];
            this.src = fallbacks[hash] + '?ixlib=rb-4.0.3&auto=format&fit=crop&w=800&q=80';
            this.onerror = null;
        };

        const addBtn = document.getElementById('modal-add-btn');
        if (addBtn) {
            const sync = () => {
                addBtn.innerHTML = state.selectedAttractionIds.has(id)
                    ? '<i class="fas fa-minus"></i> Remove from Itinerary'
                    : '<i class="fas fa-plus"></i> Add to Itinerary';
            };
            sync();
            addBtn.onclick = (e) => {
                e.stopPropagation();
                const cardBtn = document.querySelector(`#recommendations-container .toggle-select-btn[data-id="${CSS.escape(id)}"]`);
                toggleSelect(a, id, cardBtn);
                sync();
            };
        }
        
        const mapBtn = document.getElementById('modal-map-btn');
        if (mapBtn) {
            const lat = a.latitude || (a.location && a.location.lat) || (a.location && a.location.latitude);
            const lng = a.longitude || (a.location && a.location.lng) || (a.location && a.location.longitude);
            
            if (lat && lng) {
                mapBtn.disabled = false;
                mapBtn.innerHTML = '<i class="fas fa-map me-2"></i> View on Map';
                mapBtn.onclick = (e) => {
                    e.stopPropagation();
                    closeAttractionModal();
                    setTimeout(() => {
                        switchTab('map');
                        if (!state.map) initMap();
                        if (state.map) {
                            state.map.setView([lat, lng], 15);
                            L.marker([lat, lng]).addTo(state.map)
                                .bindPopup(`<b>${escapeHtml(a.name)}</b><br>${escapeHtml(fieldAddress(a))}`)
                                .openPopup();
                        }
                    }, 300);
                };
            } else {
                mapBtn.disabled = true;
                mapBtn.innerHTML = '<i class="fas fa-map-marker-slash me-2"></i> Location unavailable';
                mapBtn.onclick = null;
            }
        }

        const modalEl = document.getElementById('attractionModal');
        if (!currentAttractionModal) {
            currentAttractionModal = new bootstrap.Modal(modalEl);
        }
        
        const closeBtn = modalEl.querySelector('[data-testid="attraction-modal-close"]');
        if (closeBtn) {
            closeBtn.onclick = (e) => {
                e.stopPropagation();
                closeAttractionModal();
            };
        }
        
        currentAttractionModal.show();
    }

    function renderAccommodations(accs) {
        const container = document.getElementById('accommodation-options');
        if (!container) return;

        const tabs = document.querySelectorAll('#acc-tier-tabs button');
        tabs.forEach(btn => {
            btn.onclick = () => {
                // Ignore clicks on currently active tab
                if (btn.classList.contains('btn-success')) return;
                
                state.accTier = btn.getAttribute('data-tier');
                tabs.forEach(b => {
                    b.classList.remove('btn-success');
                    b.classList.add('btn-outline-secondary');
                });
                btn.classList.add('btn-success');
                btn.classList.remove('btn-outline-secondary');
                
                container.innerHTML = Array(3).fill(`
                    <div class="bento-card skeleton-card">
                        <div class="skeleton skeleton-img"></div>
                        <div class="skeleton skeleton-line w-60 mt-2"></div>
                        <div class="skeleton skeleton-line w-40 mt-1"></div>
                        <div class="skeleton skeleton-line w-100 mt-auto"></div>
                    </div>
                `).join('');
                
                setTimeout(() => renderAccommodations(accs), 150);
            };
        });

        const filteredAccs = state.accTier === 'all' ? accs : accs.filter(acc => {
            const tierStr = (acc.budget_tier || acc.tier || acc.category || '').toLowerCase();
            return tierStr.includes(state.accTier);
        });

        if (!Array.isArray(filteredAccs) || filteredAccs.length === 0) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-bed"></i>
                    <h5 class="Bricolage">No accommodations found</h5>
                    <p>We couldn't find matches for the selected tier.<br>Try selecting "All" or a different budget tier.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = '';
        filteredAccs.forEach(acc => {
            if (!acc) return;
            const card = document.createElement('div');
            card.className = 'bento-card position-relative';
            card.setAttribute('data-testid', 'accommodation-card');
            const selected = state.selectedAccommodationId === acc.id;
            if (selected) card.style.border = '2px solid var(--accent-color)';
            
            let badgeColor = 'bg-secondary';
            const lowerTier = (acc.budget_tier || acc.tier || 'standard').toLowerCase();
            if (lowerTier.includes('budget')) badgeColor = 'bg-warning text-dark';
            else if (lowerTier.includes('medium') || lowerTier.includes('mid')) badgeColor = 'bg-info text-dark';
            else if (lowerTier.includes('luxury')) badgeColor = 'bg-primary';
            
            const tierBadge = `<span class="badge ${badgeColor} position-absolute top-0 start-0 m-3 px-3 py-2 text-capitalize shadow-sm" style="border-radius: var(--radius-pill); font-weight: 700;">${escapeHtml(acc.budget_tier || acc.tier || '')}</span>`;
            
            let priceHtml = '';
            if (acc.price_status === 'unavailable' || !acc.price) {
                priceHtml = `
                    <button class="btn btn-sm btn-link text-accent p-0 fw-bold price-request-btn" data-testid="accommodation-price">
                        Price on request <i class="fas fa-external-link-alt ms-1 small"></i>
                    </button>
                `;
            } else {
                priceHtml = `
                    <span class="fw-bold fs-5 text-accent" data-testid="accommodation-price">₹${escapeHtml(String(acc.price))}</span>
                    <span class="text-muted small">/ night</span>
                `;
            }

            card.innerHTML = `
                <div class="position-relative">
                    <img src="${escapeHtml(acc.image_url || fieldImage(acc))}" class="bento-img" onerror="this.style.display='none'">
                    ${tierBadge}
                </div>
                <div class="bento-content">
                    <h5 class="Bricolage mb-1">${escapeHtml(acc.name || 'Stay')}</h5>
                    <p class="text-muted small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(acc.address || acc.location_name || 'Local stay')}</p>
                    <div class="d-flex align-items-center mb-2">
                        <i class="fas fa-star text-warning me-1"></i> <span class="fw-bold me-2">${acc.rating === undefined || acc.rating === null ? 'Not rated' : escapeHtml(String(acc.rating))}</span>
                    </div>
                    <p class="text-secondary small mb-3 text-truncate">${escapeHtml((acc.amenities || []).join(' · ') || 'Amenities not reported')}</p>
                    <div class="d-flex justify-content-between align-items-center mt-auto">
                        <div>${priceHtml}</div>
                        <button class="btn btn-sm ${selected ? 'btn-success' : 'btn-outline-primary'} acc-select-btn" data-testid="accommodation-select">${selected ? '<i class="fas fa-check"></i> Selected' : 'Select'}</button>
                    </div>
                </div>
            `;
            container.appendChild(card);

            const selectBtn = card.querySelector('.acc-select-btn');
            if (selectBtn) {
                selectBtn.addEventListener('click', e => {
                    e.stopPropagation();
                    // The budget is computed server-side from the selected stay;
                    // patching it in the browser created a second, wrong total.
                    if (state.budget && acc.price_per_night) {
                        showToast('Accommodation updated — regenerate the plan to refresh the budget.', 'info');
                    }
                    state.selectedAccommodationId = acc.id;
                    renderAccommodations(accs);
                });
            }

            const priceBtn = card.querySelector('.price-request-btn');
            if (priceBtn) {
                priceBtn.addEventListener('click', e => {
                    e.stopPropagation();
                    document.getElementById('modal-title').innerText = acc.name || 'Accommodation Details';
                    document.getElementById('modal-location').innerText = acc.address || 'Address Unavailable';
                    const accRatingEl = document.getElementById('modal-rating');
                    if (accRatingEl) {
                        accRatingEl.innerText = (acc.rating === undefined || acc.rating === null) ? 'Not rated' : String(acc.rating);
                    }
                    
                    let descHtml = '<strong>Live pricing is currently unavailable for this property.</strong><br><br>';
                    if (acc.booking_url) {
                        descHtml += `Check availability and pricing directly: <a href="${escapeHtml(acc.booking_url)}" target="_blank" class="text-accent text-decoration-underline">${escapeHtml(acc.booking_url)}</a><br><br>`;
                    } else {
                        descHtml += 'No booking link provided. Please contact the property directly for exact pricing.<br><br>';
                    }
                    descHtml += 'Amenities: ' + escapeHtml((acc.amenities || []).join(' · '));
                    
                    document.getElementById('modal-desc').innerHTML = descHtml;
                    
                    const img = document.getElementById('modal-image');
                    img.style.display = 'block';
                    img.src = acc.image_url || fieldImage(acc);
                    
                    const addBtn = document.getElementById('modal-add-btn');
                    if (addBtn) addBtn.style.display = 'none'; // Hide Itinerary button for accommodation
                    
                    if (!currentAttractionModal) {
                        currentAttractionModal = new bootstrap.Modal(document.getElementById('attractionModal'));
                    }
                    currentAttractionModal.show();
                    
                    // Restore addBtn when modal closes so it doesn't break attractions
                    document.getElementById('attractionModal').addEventListener('hidden.bs.modal', function onHidden() {
                        if (addBtn) addBtn.style.display = 'block';
                        document.getElementById('attractionModal').removeEventListener('hidden.bs.modal', onHidden);
                    });
                });
            }
        });
    }

    function renderFood(foodArray) {
        const container = document.getElementById('food-options');
        if (!container) return;

        const tabs = document.querySelectorAll('#food-cat-tabs button');
        tabs.forEach(btn => {
            btn.onclick = () => {
                if (btn.classList.contains('btn-success')) return;
                
                state.foodCat = btn.getAttribute('data-cat');
                tabs.forEach(b => {
                    b.classList.remove('btn-success');
                    b.classList.add('btn-outline-secondary');
                });
                btn.classList.add('btn-success');
                btn.classList.remove('btn-outline-secondary');
                
                container.innerHTML = Array(3).fill(`
                    <div class="bento-card skeleton-card">
                        <div class="skeleton skeleton-img"></div>
                        <div class="skeleton skeleton-line w-60 mt-2"></div>
                        <div class="skeleton skeleton-line w-40 mt-1"></div>
                        <div class="skeleton skeleton-line w-100 mt-auto"></div>
                    </div>
                `).join('');
                
                setTimeout(() => renderFood(state.food), 150);
            };
        });

        const safeFoods = Array.isArray(foodArray) ? foodArray : [];
        const filteredFoods = state.foodCat === 'all' ? safeFoods : safeFoods.filter(f => {
            // Meal_types is an array coming from backend e.g. ["lunch", "dinner", "street_food"]
            const mealTypes = Array.isArray(f.meal_types) ? f.meal_types : [];
            const catStr = String(f.meal_type || f.category || '').toLowerCase();
            return mealTypes.includes(state.foodCat) || catStr.includes(state.foodCat);
        });

        if (filteredFoods.length === 0) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-utensils"></i>
                    <h5 class="Bricolage">No verified dining spots are currently available for this category.</h5>
                    <p>Please select a different filter such as "All" or explore the map.</p>
                </div>
            `;
            return;
        }

        container.innerHTML = '';
        filteredFoods.forEach(s => {
            const card = document.createElement('div');
            card.className = 'bento-card position-relative';
            card.setAttribute('data-testid', 'food-card');
            
            // Price level is shown only when a source actually reports it;
            // guessing "₹₹" for every unknown place would be fabricated pricing.
            const rawLevel = parseInt(s.price_level, 10);
            const priceStr = (!isNaN(rawLevel) && rawLevel >= 1 && rawLevel <= 4) ? '₹'.repeat(rawLevel) : 'Price not reported';
            const ratingVal = fieldRating(s);
            
            const badgeType = s.source === 'geoapify' ? 'bg-primary' : 'bg-success';
            const badgeHtml = s.cuisine ? `<span class="badge ${badgeType} position-absolute top-0 start-0 m-3 px-3 py-2 text-capitalize shadow-sm" style="border-radius: var(--radius-pill); font-weight: 700;">${escapeHtml(s.cuisine)}</span>` : '';
            
            card.innerHTML = `
                <div class="position-relative">
                    <img src="${escapeHtml(s.image_url || fieldImage(s))}" class="bento-img" onerror="this.style.display='none'">
                    ${badgeHtml}
                </div>
                <div class="bento-content">
                    <h5 class="Bricolage mb-1">${escapeHtml(s.name || s.dish || 'Eatery')}</h5>
                    <p class="text-muted small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(s.location_name || s.address || 'Local area')}</p>
                    <div class="d-flex align-items-center mb-2">
                        <i class="fas fa-star text-warning me-1"></i> <span class="fw-bold me-2">${ratingVal === null ? 'Not rated' : ratingVal}</span>
                    </div>
                    <p class="text-secondary small mb-3 text-truncate">${escapeHtml(s.description || 'Local dining spot')}</p>
                    <div class="d-flex justify-content-between align-items-center mt-auto">
                        <span class="text-muted small"><i class="fas fa-utensils text-accent me-1"></i> ${escapeHtml((s.meal_types || []).map(m => m.replace('_', ' ')).join(', ') || 'Dining')}</span>
                        <span class="fw-bold text-accent">${escapeHtml(priceStr)}</span>
                    </div>
                </div>
            `;
            container.appendChild(card);
        });
    }

    function renderItinerary(itineraryData) {
        let daysArray = [];
        if (Array.isArray(itineraryData)) {
            daysArray = itineraryData;
        } else if (itineraryData && Array.isArray(itineraryData.days)) {
            daysArray = itineraryData.days;
        }

        // Same day-number contract as the map: 1 / "1" / "Day 2" all become an int.
        daysArray.forEach((d, i) => {
            const m = String(d.day ?? '').match(/(\d+)/);
            d.day = m ? parseInt(m[1], 10) : (i + 1);
        });

        const tl = document.getElementById('itinerary-timeline');
        const pills = document.getElementById('itinerary-day-pills');
        const title = document.getElementById('itinerary-title');
        if (!tl) return;
        tl.innerHTML = '';
        if (pills) pills.innerHTML = '';
        if (title) title.innerText = `${daysArray.length}-Day Itinerary`;

        if (!daysArray || daysArray.length === 0) return;

        // Initialize active day if not set or out of bounds
        let activeDayRaw = null;
        try { activeDayRaw = sessionStorage.getItem('activeItineraryDay'); } catch (e) { }
        if (activeDayRaw) state.activeItineraryDay = activeDayRaw === 'all' ? 'all' : parseInt(activeDayRaw, 10);
        else if (!state.activeItineraryDay || state.activeItineraryDay === 'all') state.activeItineraryDay = 1;
        
        if (state.activeItineraryDay !== 'all' && !daysArray.some(d => d.day === state.activeItineraryDay)) {
            state.activeItineraryDay = daysArray[0].day;
            try { sessionStorage.setItem('activeItineraryDay', String(state.activeItineraryDay)); } catch (e) { }
        }

        const getDayDateStr = (dayIdx) => {
            if (!state.startDate || state.startDate.toLowerCase() === 'flexible') return '';
            const start = new Date(state.startDate);
            if (isNaN(start.getTime())) return '';
            start.setDate(start.getDate() + dayIdx);
            return start.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
        };

        // Render Tabs
        daysArray.forEach((day, i) => {
            if (pills) {
                const btn = document.createElement('button');
                const isActive = day.day === state.activeItineraryDay;
                btn.className = `btn rounded-pill px-4 ${isActive ? 'btn-success' : 'btn-outline-secondary'}`;
                btn.innerText = `Day ${day.day}`;
                btn.onclick = () => {
                    state.activeItineraryDay = day.day;
                    try { sessionStorage.setItem('activeItineraryDay', String(day.day)); } catch (e) { }
                    renderItinerary(state.itinerary); // Re-render itinerary with new active tab
                };
                pills.appendChild(btn);
            }
        });

        // Find Active Day Data
        const activeDayIdx = daysArray.findIndex(d => d.day === state.activeItineraryDay);
        const day = daysArray[activeDayIdx] || daysArray[0];
        const dayIdx = activeDayIdx !== -1 ? activeDayIdx : 0;

        const dayDiv = document.createElement('div');
        dayDiv.id = `itinerary-day-${day.day}`;
        dayDiv.setAttribute('data-testid', `itinerary-day-${day.day}`);
        
        const dateFallback = day.date ? day.date : getDayDateStr(dayIdx);
        let displayDate = (state.startDate && state.startDate.toLowerCase() !== 'flexible') ? getDayDateStr(dayIdx) : dateFallback;
        if (day.is_suggested_date) displayDate += " (Suggested)";
        dayDiv.innerHTML = `<h4 class="Bricolage mt-4 mb-4 text-accent"><i class="fas fa-sun me-2"></i> Day ${day.day} <span class="text-secondary ms-2" style="font-size: 1.1rem; font-weight: normal;">${escapeHtml(displayDate)}</span></h4>`;
        tl.appendChild(dayDiv);

        let draggedSpotInfo = null;
        
        // Use normalized buckets if available, otherwise build them
        let buckets = {};
        if (day.morning || day.afternoon || day.evening) {
            buckets = { Morning: day.morning || [], Afternoon: day.afternoon || [], Evening: day.evening || [] };
            if (day.meals && day.meals.length > 0) buckets.Meals = day.meals;
        } else {
            buckets = { Morning: [], Afternoon: [], Evening: [], Night: [] };
            (day.activities || []).forEach((spot, spotIdx) => {
                let b = 'Afternoon';
                const t = (spot.start_time || spot.time || '').toLowerCase();
                if (t.includes('am') || t.startsWith('0') || t.startsWith('10') || t.startsWith('11')) {
                    if (t.startsWith('0') && !t.includes('am') && !t.includes('pm')) b = (parseInt(t) < 12) ? 'Morning' : 'Afternoon';
                    else if (t.includes('am')) b = 'Morning';
                }
                if (t.includes('pm')) {
                    const hr = parseInt(t.replace(/[^0-9]/g, '') || 12);
                    if (hr === 12 || hr < 5) b = 'Afternoon';
                    else if (hr >= 5 && hr < 8) b = 'Evening';
                    else b = 'Night';
                }
                buckets[b].push({ spot, spotIdx });
            });
        }
        
        const contentDiv = document.createElement('div');
        contentDiv.setAttribute('data-testid', 'itinerary-day-content');
        
        let hasContent = false;

        Object.entries(buckets).forEach(([bName, bSpots]) => {
            if (!bSpots || bSpots.length === 0) return;
            hasContent = true;
            
            const bHeader = document.createElement('div');
            bHeader.className = 'text-muted small fw-bold mb-3 ms-4 text-uppercase border-bottom pb-1';
            bHeader.innerText = bName;
            contentDiv.appendChild(bHeader);
            
            bSpots.forEach((spotObj, internalIdx) => {
                const spot = spotObj.spot || spotObj; // Handle both mapping formats
                const spotIdx = spotObj.spotIdx !== undefined ? spotObj.spotIdx : internalIdx;
                
                const item = document.createElement('div');
                item.className = 'timeline-item';
                item.id = `itinerary-item-${day.day}-${spotIdx}`;
                item.setAttribute('data-testid', 'itinerary-item');
                item.draggable = true;
                item.style.cursor = 'grab';
                
                // Allow opening modal on click
                item.onclick = (e) => {
                    // prevent dragging from firing click
                    if (e.target.closest('.timeline-dot')) return;
                    // Attempt to find full attraction info from recommendations
                    const allRecs = state.recommendations || [];
                    let fullAttr = allRecs.find(r => r.id === spot.id);
                    if (!fullAttr) fullAttr = spot;
                    openAttractionModal(fullAttr);
                };

                item.ondragstart = (e) => {
                    draggedSpotInfo = { dayIdx: dayIdx, spotIdx };
                    e.dataTransfer.effectAllowed = 'move';
                    setTimeout(() => item.style.opacity = '0.5', 0);
                };
                item.ondragend = () => item.style.opacity = '1';
                item.ondragover = (e) => e.preventDefault();
                item.ondrop = (e) => {
                    e.preventDefault();
                    if (!draggedSpotInfo) return;
                    const srcDay = draggedSpotInfo.dayIdx;
                    const srcSpot = draggedSpotInfo.spotIdx;
                    
                    // Modify spots safely
                    if (daysArray[srcDay].activities && daysArray[dayIdx].activities) {
                        const sObj = daysArray[srcDay].activities.splice(srcSpot, 1)[0];
                        let targetIdx = spotIdx;
                        if (srcDay === dayIdx && srcSpot < spotIdx) targetIdx--;
                        daysArray[dayIdx].activities.splice(targetIdx, 0, sObj);
                        
                        delete daysArray[srcDay].morning;
                        delete daysArray[srcDay].afternoon;
                        delete daysArray[srcDay].evening;
                        delete daysArray[srcDay].meals;
                        delete daysArray[dayIdx].morning;
                        delete daysArray[dayIdx].afternoon;
                        delete daysArray[dayIdx].evening;
                        delete daysArray[dayIdx].meals;

                        draggedSpotInfo = null;
                        renderItinerary(state.itinerary);
                        renderMap(state.itinerary);
                    }
                };

                item.innerHTML = `
                    <div class="timeline-dot cursor-grab"></div>
                    <div class="timeline-time">${escapeHtml(spot.start_time || '09:00 AM')}</div>
                    <div class="timeline-content">
                        ${spot.category !== 'Food' ? `<img src="${escapeHtml(fieldImage(spot))}" class="timeline-img" onerror="this.style.display='none'">` : ''}
                        <div>
                            <h5 class="Bricolage mb-1">${escapeHtml(spot.name || '')}</h5>
                            <p class="text-secondary small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(spot.category || 'Attraction')}</p>
                            <div class="d-flex gap-3 text-muted small">
                                <span><i class="fas fa-clock me-1"></i> ${escapeHtml(spot.estimated_duration || spot.duration || '')}</span>
                            </div>
                        </div>
                    </div>
                `;
                contentDiv.appendChild(item);
            });
        });
        
        if (!hasContent) {
            contentDiv.innerHTML = '<p class="text-muted fst-italic ms-4">Free day — no attractions scheduled.</p>';
        }
        
        tl.appendChild(contentDiv);

        // Requirement 8: anything the traveller selected but the planner could
        // not place must be reported with a reason - never dropped silently.
        const pending = (state.unscheduledAttractions || []).concat(day.unscheduled || []);
        if (pending.length) {
            const notice = document.createElement('div');
            notice.className = 'alert alert-warning small mt-4 mb-0';
            notice.setAttribute('data-testid', 'unscheduled-notice');
            const items = pending.map(u => {
                const nm = escapeHtml(u.name || u.attraction || 'Attraction');
                const reason = u.reason === 'not_found_in_data_providers' ? 'not found in the live attraction data'
                    : u.reason === 'no_time_left_in_day' ? 'no time was left in that day'
                    : u.reason === 'all_days_already_full' ? 'every day was already full'
                    : u.reason === 'dropped_by_planner' ? 'dropped by the day planner'
                    : (u.reason || 'not scheduled');
                const where = u.day ? ` (Day ${u.day})` : '';
                return `<li>${nm} — ${escapeHtml(reason)}${escapeHtml(where)}</li>`;
            }).join('');
            notice.innerHTML = `<i class="fas fa-triangle-exclamation me-2"></i><strong>Not included in the plan</strong>
                <ul class="mb-0 mt-1 ps-3">${items}</ul>`;
            tl.appendChild(notice);
        }
    }

    function haversine(a, b) {
        const R = 6371;
        const dLat = (b[0] - a[0]) * Math.PI / 180;
        const dLng = (b[1] - a[1]) * Math.PI / 180;
        const la1 = a[0] * Math.PI / 180, la2 = b[0] * Math.PI / 180;
        const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(h));
    }

    async function fetchOSRMRoute(points) {
        if (points.length < 2) return null;
        const coords = points.map(p => `${p[1]},${p[0]}`).join(';');
        const url = `https://router.project-osrm.org/route/v1/driving/${coords}?overview=full&geometries=geojson`;
        try {
            const res = await fetch(url);
            const data = await res.json();
            if (data.code === 'Ok' && data.routes && data.routes.length > 0) {
                return data.routes[0];
            }
        } catch (e) {
            console.warn('OSRM routing failed:', e);
        }
        return null;
    }

    async function renderMap(itineraryData) {
        let daysArray = [];
        if (Array.isArray(itineraryData)) {
            daysArray = itineraryData;
        } else if (itineraryData && Array.isArray(itineraryData.days)) {
            daysArray = itineraryData.days;
        }

        if (!state.map) initMap();

        // Normalise the day the user picked. The backend may send 1 or "1" or
        // "Day 2"; comparing those loosely against a stored filter is what made
        // "Day 2" appear empty. Everything is compared as an integer here.
        daysArray.forEach(d => {
            const m = String(d.day ?? '').match(/(\d+)/);
            d.day = m ? parseInt(m[1], 10) : (daysArray.indexOf(d) + 1);
        });

        let savedDay = sessionStorage.getItem('mapCurrentDay');
        if (savedDay) state.mapCurrentDay = savedDay === 'all' ? 'all' : parseInt(savedDay, 10);
        if (state.mapCurrentDay !== 'all' && !daysArray.some(d => d.day === state.mapCurrentDay)) {
            state.mapCurrentDay = 'all';
        }

        // Requirement 3: day filters must be generated from the itinerary
        // BEFORE the map-availability check, so the header is visible even on
        // first paint / while the Leaflet instance is still mounting. This
        // directly fixes the reported "All Days / Day 1 / Day 2 not visible".
        const filtersContainer = document.getElementById('map-day-filters');
        if (filtersContainer) {
            filtersContainer.innerHTML = '';
            const mkFilter = (label, value, testId, activeClass) => {
                const btn = document.createElement('button');
                btn.type = 'button';
                const isActive = String(state.mapCurrentDay) === String(value);
                btn.className = `btn btn-sm rounded-pill flex-shrink-0 ${isActive ? activeClass : 'btn-outline-secondary'}`;
                btn.setAttribute('aria-pressed', String(isActive));
                btn.innerText = label;
                btn.setAttribute('data-testid', testId);
                btn.onclick = () => {
                    state.mapCurrentDay = value;
                    try { sessionStorage.setItem('mapCurrentDay', String(value)); } catch (e) { }
                    renderMap(state.itinerary);
                };
                return btn;
            };
            // "All Days" is the DEFAULT view (requirement 3), not an opt-in.
            filtersContainer.appendChild(mkFilter('All Days', 'all', 'map-filter-all', 'btn-primary'));
            daysArray.forEach(day => {
                filtersContainer.appendChild(mkFilter(`Day ${day.day}`, day.day, `map-filter-day-${day.day}`, 'btn-success'));
            });
        }

        // Always drop previous layers first - even when this itinerary is empty -
        // otherwise markers from a finished/abandoned trip linger on the map.
        if (state.map) {
            state.mapMarkers.forEach(m => state.map.removeLayer(m));
            state.routePolylines.forEach(p => state.map.removeLayer(p));
        }
        state.mapMarkers = [];
        state.routePolylines = [];

        const mapTl = document.getElementById('map-timeline');
        if (mapTl) mapTl.innerHTML = '';

        if (!state.map || daysArray.length === 0) return;

        // Rapid filter switching must not let an in-flight OSRM fetch paint
        // markers for a day the user has already left.
        const renderToken = (state._mapRenderToken = (state._mapRenderToken || 0) + 1);

        let allPoints = [];
        let totalDistanceMeters = 0;
        let totalDurationSeconds = 0;
        let totalStops = 0;
        let anyRoutingFailed = false;

        const daysToRender = state.mapCurrentDay === 'all' ? daysArray : daysArray.filter(d => d.day === state.mapCurrentDay);

        let locationCounts = {};

        for (const day of daysToRender) {
            let dayPoints = [];
            if (mapTl) {
                const dayHeader = document.createElement('div');
                dayHeader.innerHTML = `<h5 class="Bricolage mt-3 mb-3 text-accent">Day ${day.day}</h5>`;
                mapTl.appendChild(dayHeader);
            }

            let markerIndex = 1;
            const dayActivities = day.activities || day.spots || [];
            dayActivities.forEach((spot, spotIdx) => {
                const lat = spot.location?.lat ?? spot.location?.latitude ?? spot.latitude;
                const lng = spot.location?.lng ?? spot.location?.longitude ?? spot.longitude;
                if (lat != null && lng != null && !isNaN(lat) && !isNaN(lng)) {
                    const pt = [Number(lat), Number(lng)];
                    
                    const locKey = pt[0].toFixed(5) + ',' + pt[1].toFixed(5);
                    const count = locationCounts[locKey] || 0;
                    locationCounts[locKey] = count + 1;
                    
                    let renderPt = [pt[0], pt[1]];
                    if (count > 0) {
                        const offset = count * 0.0003; 
                        renderPt[0] += offset * Math.cos(count * Math.PI / 4);
                        renderPt[1] += offset * Math.sin(count * Math.PI / 4);
                    }
                    
                    if (spot.category !== 'Food' || dayPoints.length === 0) {
                        dayPoints.push(pt);
                    }
                    allPoints.push(pt);
                    totalStops++;

                    const iconHtml = `<div style="background:#0284C7;color:white;border-radius:12px;padding:0 6px;height:24px;display:flex;align-items:center;justify-content:center;font-weight:bold;border:2px solid white;font-size:12px;box-shadow: 0 2px 4px rgba(0,0,0,0.3);white-space:nowrap;line-height:1;">D${day.day}-${markerIndex}</div>`;
                    const divIcon = L.divIcon({ html: iconHtml, className: '', iconSize: [44,24], iconAnchor: [22,12] });
                    const m = L.marker(renderPt, { 
                        icon: divIcon, 
                        title: `Day ${day.day} - ${spot.name || 'Activity'}`
                    }).addTo(state.map);
                    
                    m.on('click', () => {
                        document.getElementById('map-timeline')?.classList.add('d-none');
                        document.getElementById('map-location-details')?.classList.remove('d-none');
                        const t = document.getElementById('map-detail-title'); if (t) t.innerText = spot.name || '';
                        const d = document.getElementById('map-detail-desc'); 
                        if (d) {
                            d.innerHTML = `<strong>Day ${day.day}</strong> &bull; ${spot.time || spot.start_time || 'Time TBD'}<br/><em>${spot.category || 'Attraction'}</em><br/><br/>${spot.description || ''}`;
                        }
                        const im = document.getElementById('map-detail-img'); if (im) { im.src = fieldImage(spot); im.style.display = ''; im.onerror = () => { im.style.display = 'none'; }; }
                        state.map.setView(renderPt, 15);
                        
                        // Highlight itinerary item if it exists
                        const itemDiv = document.getElementById(`itinerary-item-${day.day}-${spotIdx}`);
                        if (itemDiv) {
                            itemDiv.scrollIntoView({behavior: 'smooth', block: 'center'});
                            itemDiv.classList.add('bg-warning', 'bg-opacity-25', 'transition-all');
                            setTimeout(() => itemDiv.classList.remove('bg-warning', 'bg-opacity-25'), 2000);
                        }
                    });
                    state.mapMarkers.push(m);

                    if (mapTl) {
                        const item = document.createElement('div');
                        item.className = 'timeline-item cursor-pointer';
                        item.innerHTML = `
                            <div class="timeline-dot bg-primary text-white" style="width:20px;height:20px;display:flex;align-items:center;justify-content:center;font-size:10px;left:-4px;">${markerIndex}</div>
                            <div class="timeline-time">${escapeHtml(spot.time || spot.start_time || '09:00 AM')}</div>
                            <div class="timeline-content p-2 ps-3">
                                <h6 class="Bricolage mb-1">${escapeHtml(spot.name || '')}</h6>
                                <p class="text-secondary small mb-0">${escapeHtml(spot.category || 'Attraction')}</p>
                            </div>
                        `;
                        item.onclick = () => m.fire('click');
                        mapTl.appendChild(item);
                    }
                    markerIndex++;
                }
            });

            if (!dayPoints.length && mapTl) {
                // An empty day is a legitimate plan state - say so instead of
                // leaving a blank panel that looks like a rendering failure.
                const empty = document.createElement('div');
                empty.className = 'text-secondary small mb-3';
                empty.innerText = `Day ${day.day} has no mapped stops.`;
                mapTl.appendChild(empty);
            }

            if (dayPoints.length > 1) {
                const routeData = await fetchOSRMRoute(dayPoints);
                // The user may have switched the day filter while OSRM thought.
                if (renderToken !== state._mapRenderToken) return;
                if (routeData && routeData.geometry) {
                    const routeLayer = L.geoJSON(routeData.geometry, {
                        style: { color: '#0EA5E9', weight: 4, opacity: 0.8 }
                    }).addTo(state.map);
                    state.routePolylines.push(routeLayer);
                    totalDistanceMeters += routeData.distance || 0;
                    totalDurationSeconds += routeData.duration || 0;
                } else {
                    anyRoutingFailed = true;
                    for (let i = 1; i < dayPoints.length; i++) totalDistanceMeters += haversine(dayPoints[i - 1], dayPoints[i]) * 1000;
                }
            }
        }

        const errEl = document.getElementById('route-unavailable-state');
        if (errEl) {
            if (anyRoutingFailed) errEl.classList.remove('d-none');
            else errEl.classList.add('d-none');
        }

        const distEl = document.getElementById('map-total-distance');
        if (distEl) distEl.innerText = totalStops > 1 ? `${(totalDistanceMeters / 1000).toFixed(1)} km` : '—';

        const timeEl = document.getElementById('map-travel-time');
        if (timeEl) {
            if (totalDurationSeconds > 0 && !anyRoutingFailed) {
                const mins = Math.round(totalDurationSeconds / 60);
                const h = Math.floor(mins / 60);
                const m = mins % 60;
                timeEl.innerText = h > 0 ? `${h}h ${m}m` : `${m}m`;
            } else {
                timeEl.innerText = '—';
            }
        }

        const stopsEl = document.getElementById('map-total-stops');
        if (stopsEl) stopsEl.innerText = totalStops || '—';

        if (allPoints.length > 0) {
            state.map.fitBounds(L.latLngBounds(allPoints), { padding: [50, 50] });
        }
    }

    function renderBudget(budget) {
        const container = document.getElementById('budget-breakdown');
        if (!container) return;
        budget = budget || {};
        // ONE canonical breakdown (services/budget_norm.normalize_budget) drives
        // the page, the API and the email. The raw BudgetAgent dict is only used
        // when the normalized object is genuinely absent.
        const nb = (budget.budget_normalized && Object.keys(budget.budget_normalized).length)
            ? budget.budget_normalized : null;

        const warningContainer = document.getElementById('budget-warning-container');
        if (warningContainer) {
            if (budget.budget_warning) {
                warningContainer.innerHTML = `<i class="fas fa-exclamation-triangle me-2"></i> ${escapeHtml(budget.budget_warning)}`;
                warningContainer.classList.remove('d-none');
                
                // Add adjustable alternatives hint
                if (budget.budget_warning.toLowerCase().includes('exceeds')) {
                    warningContainer.innerHTML += '<div class="mt-2 small fw-bold">Tip: Try selecting a cheaper transport mode or budget accommodation in the Itinerary tabs.</div>';
                }
            } else {
                warningContainer.classList.add('d-none');
                warningContainer.innerHTML = '';
            }
        }

        const currencySelect = document.getElementById('budget-currency');
        // All costs are computed and reported in INR. Converting them with an
        // invented "mock rate" would put numbers on the page that no source
        // produced, so the display stays in ₹ regardless of the selector.
        const currency = 'INR';
        const rate = 1;
        const sym = '₹';
        if (currencySelect) {
            currencySelect.value = 'INR';
            currencySelect.disabled = true;
            currencySelect.title = 'Trip costs are estimated in INR (₹)';
        }

        if (currencySelect && !currencySelect.dataset.listener) {
            currencySelect.dataset.listener = 'true';
            currencySelect.addEventListener('change', () => {
                if (state.budget) renderBudget(state.budget);
            });
        }

        const totalEl = document.getElementById('total-budget-display');
        const rawTotal = nb ? nb.total : (budget.total ?? budget.total_cost ?? budget.estimated_total ?? null);
        const total = rawTotal == null ? 0 : rawTotal;
        const convertedTotal = total * rate;
        if (totalEl) {
            totalEl.innerText = (nb && nb.total_is_valid === false) || rawTotal == null
                ? 'Not available'
                : `${sym}${Math.round(convertedTotal).toLocaleString('en-US')}`;
        }

        // car_rental and fuel are deliberately NOT listed separately: the
        // normalized local_transport figure already includes them, and showing
        // both would double-count the same money.
        const allowedCategories = ['accommodation', 'food', 'transport', 'local_transport', 'attractions', 'miscellaneous'];
        const missingCategories = new Set(nb && Array.isArray(nb.missing_categories) ? nb.missing_categories : []);
        const categoryLabels = {
            accommodation: 'Accommodation', food: 'Food', transport: 'Intercity transport',
            local_transport: 'Local transport', attractions: 'Activities / entry fees',
            miscellaneous: 'Miscellaneous'
        };
        const colors = {
            accommodation: '#0284C7', food: '#0EA5E9', local_transport: '#38BDF8',
            attractions: '#059669', car_rental: '#7C3AED', fuel: '#DB2777',
            transport: '#0891B2', intercity_transport: '#0891B2', miscellaneous: '#D97706'
        };

        const valueFor = (key) => {
            if (nb) {
                // Requirement 5: a category the providers never reported is
                // UNKNOWN, which is not the same thing as a genuine ₹0. The
                // normalized object lists the unknown ones explicitly, so a
                // reported zero stays on the page and an unknown one is called
                // out below instead of silently vanishing.
                if (missingCategories.has(key)) return null;
                const v = nb[key];
                return (typeof v === 'number' && !isNaN(v)) ? v : null;
            }
            let v = budget[key];
            // Legacy aliases emitted by BudgetAgent before normalization existed.
            if (v == null && key === 'attractions') v = budget.activities;
            if (v == null && key === 'transport') v = budget.intercity_transport;
            return (typeof v === 'number' && v > 0) ? v : null;
        };

        const entries = allowedCategories
            .map(k => [k, valueFor(k)])
            .filter(([, v]) => v != null);
        const sum = entries.reduce((acc, [, v]) => acc + v, 0) || 1;

        let summaryHtml = '';
        const limitAmt = (nb && nb.budget_limit != null) ? nb.budget_limit : (budget.budget_amount || budget.budget_limit);
        if (limitAmt) {
            const limitConverted = limitAmt * rate;
            const limitLabel = `<div class="d-flex justify-content-between mb-2 pb-2 border-bottom"><span class="fw-bold text-dark" data-testid="budget-limit">Budget Limit:</span> <span class="fw-bold">${sym}${Math.round(limitConverted).toLocaleString('en-US')}</span></div>`;

            let statusHtml = '';
            const remainingAmt = (nb && nb.remaining_budget != null) ? nb.remaining_budget
                : (budget.remaining != null) ? budget.remaining
                : (budget.remaining_budget != null ? budget.remaining_budget : null);
            const remainingConverted = remainingAmt != null ? Math.abs(remainingAmt) * rate : 0;
            const status = nb ? nb.status : (remainingAmt != null && remainingAmt < 0 ? 'over' : 'within');
            if (status === 'incomplete' || remainingAmt == null) {
                statusHtml = `<div class="d-flex justify-content-between mb-2"><span class="text-secondary fw-bold">Remaining:</span> <span class="text-secondary fw-bold">Unknown (incomplete data)</span></div>`;
            } else if (remainingAmt >= 0) {
                statusHtml = `<div class="d-flex justify-content-between mb-2"><span class="text-success fw-bold" data-testid="budget-remaining">Remaining:</span> <span class="text-success fw-bold">${sym}${Math.round(remainingConverted).toLocaleString('en-US')}</span></div>`;
            } else {
                statusHtml = `<div class="d-flex justify-content-between mb-2"><span class="text-danger fw-bold" data-testid="budget-remaining">Over budget:</span> <span class="text-danger fw-bold">${sym}${Math.round(remainingConverted).toLocaleString('en-US')}</span></div>`;
            }

            const pct = (nb && nb.utilization_percentage) || budget.utilization_percentage || Math.round((sum / limitAmt) * 100);
            const pctHtml = `<div class="d-flex justify-content-between mb-4 pb-2 border-bottom"><span class="text-secondary small">Utilization:</span> <span class="text-secondary small">${pct}% used</span></div>`;

            summaryHtml = limitLabel + statusHtml + pctHtml;
        }

        const legendHtml = entries.map(([k, v]) => {
            const label = categoryLabels[k] || k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const color = colors[k.toLowerCase()] || '#94A3B8';
            const pct = Math.round((v / sum) * 100);
            const convertedV = v * rate;
            return `
                <div class="legend-item d-flex justify-content-between align-items-center mb-2" data-testid="budget-category">
                    <div><span class="legend-color rounded-circle d-inline-block me-2 shadow-sm" style="background: ${color}; width: 12px; height: 12px;"></span> <span class="fw-bold">${escapeHtml(label)}</span></div>
                    <div class="text-secondary">${v === 0 ? '₹0 \u00b7 reported' : sym + Math.round(convertedV).toLocaleString('en-US') + ' · ' + pct + '%'}</div>
                </div>
            `;
        }).join('');

        // Names the categories no provider reported, so the traveller knows the
        // total may be understated instead of assuming those costs are zero.
        const unknownList = allowedCategories.filter(k => missingCategories.has(k))
            .map(k => categoryLabels[k] || k.replace(/_/g, ' '));
        const unknownHtml = unknownList.length
            ? `<div class="alert alert-secondary small mt-3 mb-0" data-testid="budget-unknown-categories">
                   <i class="fas fa-circle-info me-2"></i>No cost data was reported for:
                   ${escapeHtml(unknownList.join(', '))}. The total above excludes them.</div>`
            : '';

        container.innerHTML = summaryHtml + legendHtml + unknownHtml;

        // Requirement 5: render the personalised closing greeting + destination
        // tips immediately below the existing breakdown. Never overwrites the
        // categories or totals above — the same data-source rules apply (only
        // fields the backend actually produced are shown). Skipped when there
        // is no budget object yet (empty-state budget tab).
        try { renderBudgetClosing(); } catch (e) { console.warn('budget closing render failed', e); }

        const canvas = document.getElementById('budget-donut-chart');
        if (canvas && entries.length) {
            if (window.budgetChartInstance) {
                window.budgetChartInstance.destroy();
            }

            const chartData = entries.map(([k, v]) => ({
                label: categoryLabels[k] || k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
                value: v * rate,
                color: colors[k.toLowerCase()] || '#94A3B8'
            }));

            const ctx = canvas.getContext('2d');
            window.budgetChartInstance = new Chart(ctx, {
                type: 'doughnut',
                data: {
                    labels: chartData.map(d => d.label),
                    datasets: [{
                        data: chartData.map(d => d.value),
                        backgroundColor: chartData.map(d => d.color),
                        borderWidth: 0
                    }]
                },
                options: {
                    responsive: true,
                    maintainAspectRatio: false,
                    cutout: '75%',
                    plugins: {
                        legend: { display: false },
                        tooltip: {
                            callbacks: {
                                label: function(context) {
                                    let label = context.label || '';
                                    if (label) {
                                        label += ': ';
                                    }
                                    if (context.raw !== null) {
                                        label += sym + Math.round(context.raw).toLocaleString('en-US');
                                    }
                                    const pct = Math.round((context.raw / (sum * rate)) * 100);
                                    label += ` (${pct}%)`;
                                    return label;
                                }
                            }
                        }
                    }
                }
            });
        }
    }

    function renderBudgetClosing() {
        // Requirement 5: personalised, data-driven closing section rendered
        // *below* the budget breakdown. Every claim is derived from state the
        // backend actually produced (destination, days, interests, weather,
        // tips). Nothing here invents live prices, current weather, or
        // official advisories — when data is missing we render general,
        // clearly-labelled advice instead of pretending to know.
        const wrap = document.getElementById('budget-closing');
        if (!wrap) return;

        const dest = state.destination || 'your destination';
        const days = state.days;
        const people = (state.travelers && state.travelers.people) || null;
        const kids = (state.travelers && state.travelers.kids) || null;
        const startDate = state.startDate;
        const interests = (state.interests || []).filter(Boolean);
        // The itinerary reaches the client as {days: [...]}; older payloads sent
        // a bare array. Accept both so the closing summary never goes blank.
        const itinerary = Array.isArray(state.itinerary) ? state.itinerary
            : ((state.itinerary && Array.isArray(state.itinerary.days)) ? state.itinerary.days : []);
        const tips = state.travelTips || (state.budget && (state.budget.tips || state.budget.travel_tips)) || '';
        const weather = state.weather;
        const budgetTotal = (state.budget && (state.budget.total ?? state.budget.total_cost ?? state.budget.estimated_total)) || 0;

        // Pick a small set of genuine highlights from the itinerary, if any.
        const highlights = [];
        for (const d of itinerary) {
            for (const a of (d.activities || [])) {
                if (a && a.name && a.category !== 'Food' && !a.is_meal) {
                    highlights.push(a.name);
                    if (highlights.length >= 5) break;
                }
            }
            if (highlights.length >= 5) break;
        }

        const greetingBits = [];
        greetingBits.push('Your adventure is taking shape!');
        if (dest) greetingBits.push(`Here is the current shape of your ${days ? days + '-day ' : ''}trip to ${escapeHtml(dest)}.`);
        if (people) {
            const grp = `${escapeHtml(String(people))} traveller${String(people) === '1' ? '' : 's'}` +
                        (kids && String(kids) !== '0' ? ` (+${escapeHtml(String(kids))} kid${String(kids) === '1' ? '' : 's'})` : '');
            greetingBits.push(`Planning for ${grp}.`);
        }
        if (startDate && String(startDate).toLowerCase() !== 'flexible') {
            greetingBits.push(`Departing around ${escapeHtml(String(startDate))}.`);
        }

        const summaryLines = [];
        if (interests.length) {
            summaryLines.push(`<div><i class="fas fa-heart text-danger me-2"></i>Interests you shared: <strong>${escapeHtml(interests.join(', '))}</strong></div>`);
        }
        if (highlights.length) {
            summaryLines.push(`<div class="mt-1"><i class="fas fa-star text-warning me-2"></i>Planned highlights: ${escapeHtml(highlights.join(' · '))}</div>`);
        }
        if (budgetTotal) {
            summaryLines.push(`<div class="mt-1"><i class="fas fa-wallet text-accent me-2"></i>Estimated total: <strong>₹${Math.round(budgetTotal).toLocaleString('en-US')}</strong></div>`);
        }

        // Weather/seasonal line — only when we actually have a forecast.
        let weatherLine = '';
        try {
            const daily = weather && (Array.isArray(weather) ? weather : (weather.daily_forecast || []));
            if (daily && daily.length) {
                const first = daily[0] || {};
                const cond = first.condition || '';
                const tmax = first.max_temp || '';
                if (cond || tmax) {
                    weatherLine = `<div class="mt-3"><i class="fas fa-cloud-sun text-info me-2"></i>Starting-week outlook: ${escapeHtml(cond || 'mixed')}${tmax ? `, high ~${escapeHtml(String(tmax))}` : ''}. Confirm the forecast closer to departure.</div>`;
                }
            } else {
                weatherLine = `<div class="mt-3 text-muted small"><i class="fas fa-cloud-sun me-2"></i>No live forecast was available for ${escapeHtml(dest)} when this plan was generated — check a weather service before you pack.</div>`;
            }
        } catch (_) { /* never fail the whole page for the tips block */ }

        // General destination-agnostic tips (clearly framed as general).
        const generalTips = [
            'Confirm transport tickets, hotel check-in times and any required permits close to your travel dates.',
            'Keep a small buffer each day — road conditions, queues and weather change plans.',
            'Carry a copy (offline or printed) of this itinerary and emergency contacts.',
            'Respect local customs, opening hours and photography rules at religious and heritage sites.',
            'Use reputable operators for taxis, rentals and guided experiences; agree fares up front.',
            'Pack for mixed conditions: layering, comfortable shoes, sunscreen, and a light rain shell are almost always useful.'
        ];

        const reminder = `<div class="mt-3 p-3 rounded-3" style="background:#fffbeb;border-left:4px solid #f59e0b;">
            <strong><i class="fas fa-exclamation-triangle me-1"></i> Before you go:</strong>
            Prices, schedules and availability change. Re-verify anything that matters (flights, trains, hotel, permits, entry fees) with the official provider before travelling.
        </div>`;

        const actionsBar = `<div class="d-flex gap-2 flex-wrap mt-4">
            <button class="btn btn-outline rounded-pill" id="budget-closing-view-itinerary"><i class="fas fa-calendar-alt me-2"></i>View itinerary</button>
            <button class="btn btn-outline rounded-pill" id="budget-closing-view-map"><i class="fas fa-map me-2"></i>View on map</button>
            <button class="btn btn-outline rounded-pill" id="budget-closing-new-trip"><i class="fas fa-plus me-2"></i>Start a new trip</button>
        </div>`;

        const tipsHtml = tips ? `<div class="mt-3"><div class="fw-bold mb-1"><i class="fas fa-lightbulb text-warning me-2"></i>Travel tips for ${escapeHtml(dest)}</div><div style="white-space:pre-wrap;">${escapeHtml(String(tips))}</div></div>` : '';

        wrap.innerHTML = `
            <div class="glass p-4 rounded-4 border">
                <h4 class="Bricolage mb-2"><i class="fas fa-paper-plane text-accent me-2"></i>Wrap-up &amp; travel tips</h4>
                <p class="mb-2">${greetingBits.join(' ')}</p>
                <div>${summaryLines.join('')}</div>
                ${weatherLine}
                ${tipsHtml}
                <div class="mt-3">
                    <div class="fw-bold mb-1"><i class="fas fa-compass me-2 text-accent"></i>General reminders (apply to any destination)</div>
                    <ul class="mb-0 ps-4">${generalTips.map(t => `<li class="mb-1">${escapeHtml(t)}</li>`).join('')}</ul>
                </div>
                ${reminder}
                ${actionsBar}
            </div>
        `;
        wrap.classList.remove('d-none');

        document.getElementById('budget-closing-view-itinerary')?.addEventListener('click', () => switchTab('itinerary'));
        document.getElementById('budget-closing-view-map')?.addEventListener('click', () => { switchTab('map'); if (!state.map) initMap(); renderMap(state.itinerary); });
        document.getElementById('budget-closing-new-trip')?.addEventListener('click', () => { if (typeof resetTrip === 'function') resetTrip(); });
    }

    function renderWeather(weatherData) {
        const container = document.getElementById('weather-container');
        if (!container) return;
        container.innerHTML = '';

        if (!weatherData || (!weatherData.daily_forecast && !Array.isArray(weatherData))) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-cloud-sun text-warning"></i>
                    <h5 class="Bricolage">Weather unavailable</h5>
                    <p>Weather forecast is currently unavailable for this destination.</p>
                </div>
            `;
            return;
        }

        let dailyForecast = weatherData.daily_forecast || (Array.isArray(weatherData) ? weatherData : []);
        
        if (!Array.isArray(dailyForecast) || dailyForecast.length === 0) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-cloud-sun text-warning"></i>
                    <h5 class="Bricolage">Weather unavailable</h5>
                    <p>Weather forecast is currently unavailable for this destination.</p>
                </div>
            `;
            return;
        }

        if (weatherData.summary) {
            const overviewDiv = document.createElement('div');
            overviewDiv.className = 'weather-overview mb-4 p-4 glass rounded-4 border';
            overviewDiv.innerHTML = `
                <h4 class="Bricolage text-main mb-3"><i class="fas fa-info-circle me-2 text-accent"></i>Weather overview</h4>
                <p class="mb-3 text-secondary fs-5">${escapeHtml(weatherData.summary)}</p>
                <div class="small text-muted d-flex flex-column gap-2 mb-3">
                    ${weatherData.temperature_summary ? `<div><i class="fas fa-thermometer-half me-2 text-danger"></i> ${escapeHtml(weatherData.temperature_summary)}</div>` : ''}
                    ${weatherData.precipitation_summary ? `<div><i class="fas fa-umbrella me-2 text-primary"></i> ${escapeHtml(weatherData.precipitation_summary)}</div>` : ''}
                    ${weatherData.wind_summary ? `<div><i class="fas fa-wind me-2 text-info"></i> ${escapeHtml(weatherData.wind_summary)}</div>` : ''}
                    ${weatherData.uv_summary ? `<div><i class="fas fa-sun me-2 text-warning"></i> ${escapeHtml(weatherData.uv_summary)}</div>` : ''}
                </div>
                ${weatherData.recommendation ? `<div class="p-3 bg-light rounded border fw-bold text-dark"><i class="fas fa-suitcase-rolling me-2 text-accent"></i> Travel Advice: ${escapeHtml(weatherData.recommendation)}</div>` : ''}
            `;
            container.appendChild(overviewDiv);
        }

        const forecastHeading = document.createElement('h4');
        forecastHeading.className = 'Bricolage text-main mb-3';
        forecastHeading.innerText = 'Complete Forecast';
        container.appendChild(forecastHeading);

        const strip = document.createElement('div');
        strip.className = 'weather-strip d-flex overflow-auto gap-3 pb-2';

        dailyForecast.slice(0, 5).forEach(day => {
            const card = document.createElement('div');
            card.className = 'glass p-3 rounded-4 flex-shrink-0 text-center border position-relative';
            card.style.minWidth = '220px';

            let dateStr = day.date;
            try {
                const d = new Date(day.date);
                dateStr = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
            } catch (e) { }

            let iconClass = 'fa-cloud-sun';
            const cond = (day.condition || '').toLowerCase();
            if (cond.includes('clear') || cond.includes('sun')) iconClass = 'fa-sun text-warning';
            else if (cond.includes('rain') || cond.includes('drizzle')) iconClass = 'fa-cloud-rain text-primary';
            else if (cond.includes('snow')) iconClass = 'fa-snowflake text-info';
            else if (cond.includes('thunder')) iconClass = 'fa-bolt text-warning';
            else if (cond.includes('cloud') || cond.includes('overcast')) iconClass = 'fa-cloud text-secondary';

            card.innerHTML = `
                <div class="fw-bold mb-1">${escapeHtml(dateStr)}</div>
                <div class="text-muted small mb-2 text-truncate" title="${escapeHtml(day.condition || 'Unknown')}">${escapeHtml(day.condition || 'Unknown')}</div>
                <div class="fs-1 mb-2"><i class="fas ${iconClass}"></i></div>
                <div class="fw-bold fs-5">${escapeHtml(day.max_temp ?? '')}</div>
                <div class="text-muted small border-bottom pb-2 mb-2">${escapeHtml(day.min_temp ?? '')}</div>
                
                <div class="d-flex flex-column text-muted small mb-3 text-start px-2 gap-1">
                    <div title="Rain Probability"><i class="fas fa-cloud-showers-heavy text-primary me-2"></i> ${escapeHtml(day.precipitation_probability || '0%')} chance of rain</div>
                    <div title="Precipitation"><i class="fas fa-tint text-info me-2"></i> ${escapeHtml(day.precipitation || '0 mm')}</div>
                    <div title="Wind"><i class="fas fa-wind text-secondary me-2"></i> ${escapeHtml(day.wind_speed || 'N/A')}</div>
                    <div title="UV Index"><i class="fas fa-sun text-warning me-2"></i> UV: ${escapeHtml(day.uv_index || '0')}</div>
                </div>
                
                <div class="mt-auto">
                    <div class="badge bg-light text-dark border w-100 text-wrap py-2" title="${escapeHtml(day.packing_advice || 'Comfortable shoes')}"><i class="fas fa-suitcase me-1"></i> ${escapeHtml(day.packing_advice || 'Comfortable shoes')}</div>
                </div>
            `;
            strip.appendChild(card);
        });

        container.appendChild(strip);
    }

    // ── Transit display helpers ──────────────────────────────────────────────
    // Costs arrive with their own currency code. Hard-coding '₹' made a fare the
    // agent had quoted in USD display as rupees (a "$320" Delhi->Dubai flight
    // appeared as ₹320), so a value is always shown in the currency it was
    // quoted in. Nothing is converted here - we have no live FX feed and a
    // made-up rate would be a fabrication.
    const CURRENCY_SYMBOLS = { INR: '₹', USD: '$', EUR: '€', GBP: '£', AED: 'AED ', SGD: 'S$ ', THB: '฿' };
    function money(value, currency) {
        const n = Number(value);
        if (value === null || value === undefined || !isFinite(n)) return 'Price unavailable';
        const code = String(currency || 'INR').toUpperCase();
        const sym = CURRENCY_SYMBOLS[code] !== undefined ? CURRENCY_SYMBOLS[code] : (code + ' ');
        return sym + n.toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }
    const BASIS_LABELS = {
        per_person: 'per person', per_vehicle: 'per vehicle', per_group: 'per group',
        per_day: 'per day', per_trip: 'per trip', per_night: 'per night'
    };
    function basisLabel(basis) {
        if (!basis) return '';
        return BASIS_LABELS[String(basis)] || String(basis).replace(/_/g, ' ');
    }
    // Which journey a card prices - previously every card was just "Flight".
    function transitLegText(opt, transitData) {
        const origin = String((opt && opt.origin) || (transitData && transitData.origin) || state.origin || '').trim();
        const destination = String((opt && opt.destination) || (transitData && transitData.destination) || state.destination || '').trim();
        if (origin && destination) return origin + ' → ' + destination;
        return destination || origin || '';
    }
    function transitPriceBlock(opt, transitData, isIntercity) {
        const value = opt.estimated_cost != null ? opt.estimated_cost
            : (opt.price_inr != null ? opt.price_inr : null);
        const currency = opt.currency || (transitData && transitData.currency) || 'INR';
        if (value === null || opt.cost_status === 'unavailable') {
            return '<div class="fw-bold fs-6 text-muted">Price unavailable</div>';
        }
        const basis = basisLabel(opt.cost_basis);
        const qualifier = [basis, isIntercity ? 'one way' : ''].filter(Boolean).join(' · ');
        let html = `<div class="fw-bold fs-5 text-accent">${escapeHtml(money(value, currency))}` +
            (qualifier ? ` <small class="fw-normal text-muted fs-6">(${escapeHtml(qualifier)})</small>` : '') +
            '</div>';
        if (isIntercity && opt.group_cost != null && String(opt.cost_basis) === 'per_person') {
            const travellers = (transitData && transitData.num_people) ||
                (state.travelers && state.travelers.people) ||
                (value ? Math.round(Number(opt.group_cost) / Number(value)) : null);
            if (travellers) {
                html += `<div class="small text-muted">≈ ${escapeHtml(money(opt.group_cost, currency))} for ${escapeHtml(String(travellers))} traveller(s), one way</div>`;
            }
        }
        if (opt.cost_status === 'non_inr_currency') {
            html += '<div class="small text-warning" data-testid="transit-non-inr">Not priced in ₹ - excluded from the budget total.</div>';
        }
        return html;
    }

    function renderTransport(transitData) {
        const container = document.getElementById('transport-options');
        if (!container) return;
        container.innerHTML = '';

        if (!transitData) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-bus"></i>
                    <h5 class="Bricolage">No transport options available</h5>
                    <p>We couldn't find any transit options from your origin.</p>
                </div>
            `;
            return;
        }

        // Requirement 4: because ExploreX does not integrate a live booking
        // feed, every option shown here is an AI *estimate*. Surface that
        // once, prominently, above the list — the per-card "Estimated" chip
        // then reinforces it on every row.
        const isEstimate = !!transitData.is_estimate || !!transitData.estimation_note;
        if (isEstimate) {
            const note = String(transitData.estimation_note || 'Values shown are AI-generated estimates for planning purposes. Actual prices, schedules and seat availability must be confirmed with the operating carrier or booking platform.').slice(0, 400);
            const banner = document.createElement('div');
            banner.className = 'alert alert-warning d-flex align-items-start gap-2 mb-3';
            banner.setAttribute('data-testid', 'transit-estimate-banner');
            banner.innerHTML = '<i class="fas fa-hourglass-half mt-1"></i><div><strong>Planning estimates, not live bookings.</strong><br><span class="small">' + escapeHtml(note) + '</span></div>';
            container.appendChild(banner);
        }

        const options = Array.isArray(transitData.options) ? transitData.options
            : (Array.isArray(transitData.intercity_transport) ? transitData.intercity_transport : []);
        const localOptions = Array.isArray(transitData.local_transport) ? transitData.local_transport : [];
        const recommendedMode = String(transitData.recommended_mode || '').toLowerCase();
        const typeIcons = {
            flight: 'fa-plane', train: 'fa-train', bus: 'fa-bus', car: 'fa-car', driving: 'fa-car',
            self_drive: 'fa-car-side', taxi: 'fa-taxi', taxi_or_cab_transfer: 'fa-taxi',
            rental_car: 'fa-car-rear', app_cab: 'fa-mobile-screen', public_transit: 'fa-train-subway',
            auto_rickshaw: 'fa-taxi', walk_or_cycle: 'fa-walking'
        };

        if (options.length === 0) {
            // A same-city trip legitimately has no intercity leg. Saying "we
            // couldn't find any transit options" implied a lookup failure, so
            // the reason is stated from the backend flag instead.
            const sameCity = !!transitData.same_city;
            const empty = document.createElement('div');
            empty.className = 'empty-state-card';
            empty.setAttribute('data-testid', 'transit-empty');
            if (sameCity) {
                empty.innerHTML = `
                    <i class="fas fa-location-dot"></i>
                    <h5 class="Bricolage">No intercity journey needed</h5>
                    <p>Your origin and destination are the same city, so this trip has no
                       outbound leg to price. In-city transport is listed below
                       ${localOptions.length === 0 ? 'once the plan provides it.' : '.'}</p>
                `;
            } else {
                const failed = String(transitData.provider_status || '') === 'error';
                empty.innerHTML = `
                    <div class="empty-state-card">
                        <i class="fas fa-bus"></i>
                        <h5 class="Bricolage">${failed ? 'Transport options unavailable' : 'No transport options available'}</h5>
                        <p>${failed
                    ? `We could not generate fares for ${escapeHtml(transitLegText({}, transitData) || 'this route')} right now. No prices are shown because none are guessed - regenerate the plan to try again.`
                    : `We found no intercity option for ${escapeHtml(transitLegText({}, transitData) || 'this route')}. In-city transport is listed below once the plan provides it.`}</p>
                    </div>
                `;
            }
            container.appendChild(empty);
            // Still show the local options - they are independent of the
            // intercity leg and were previously hidden by this early return.
            if (localOptions.length > 0) renderLocalTransit(container, transitData, localOptions, typeIcons);
            return;
        }

        // Name the journey before pricing it. The section used to open straight
        // into "Flight / Train" cards, so nothing said which route was quoted.
        const routeHeading = document.createElement('h5');
        routeHeading.className = 'Bricolage mb-1';
        routeHeading.setAttribute('data-testid', 'transit-route');
        const leg = transitLegText({}, transitData);
        routeHeading.innerHTML = '<i class="fas fa-location-arrow text-accent me-2"></i>' +
            escapeHtml(leg ? `Getting from ${leg}` : 'Intercity options');
        container.appendChild(routeHeading);
        const routeSub = document.createElement('p');
        routeSub.className = 'text-secondary small mb-3';
        const travellers = transitData.num_people || (state.travelers && state.travelers.people);
        routeSub.textContent = 'One-way fares per traveller for ' + (leg || 'this journey') +
            (travellers ? ` · group of ${travellers}` : '') +
            '. Your budget counts a return trip (outbound + return).';
        container.appendChild(routeSub);

        // Sort options to put the recommended mode first
        const sortedOptions = [...options].sort((a, b) => {
            const aIsRec = a.mode && a.mode.toLowerCase() === recommendedMode;
            const bIsRec = b.mode && b.mode.toLowerCase() === recommendedMode;
            if (aIsRec && !bIsRec) return -1;
            if (!aIsRec && bIsRec) return 1;
            return 0;
        });

        sortedOptions.forEach((opt, idx) => {
            const type = String(opt.mode || 'route');
            const icon = typeIcons[type.toLowerCase()] || 'fa-route';
            const card = document.createElement('div');
            const isRecommended = type.toLowerCase() === recommendedMode;
            const testId = `transit-${type.toLowerCase()}`;
            
            let recommendedBadge = '';
            if (isRecommended) {
                recommendedBadge = `<span class="badge bg-warning text-dark position-absolute top-0 end-0 m-3" data-testid="transit-recommended"><i class="fas fa-star me-1"></i> Recommended option</span>`;
            }

            card.className = 'glass p-4 rounded-4 d-flex align-items-center justify-content-between mb-3 position-relative';
            card.setAttribute('data-testid', 'transit-option');
            
            const isSelected = state.selectedTransportIdx === idx;
            if (isSelected) card.style.border = '2px solid var(--accent-color)';

            const durationText = opt.duration || 'Unknown duration';
            const providerText = opt.provider || 'Provider unknown';
            const baggageText = opt.baggage || 'Baggage info unavailable';
            const ecoFriendlyText = opt.eco_friendly ? 'Eco-friendly' : 'Standard emissions';
            const ecoFriendlyClass = opt.eco_friendly ? 'text-success' : 'text-secondary';
            const legText = transitLegText(opt, transitData);
            const windowText = [opt.departure_info, opt.arrival_info].filter(Boolean).join(' → ');
            // Requirement 4B: never imply live inventory. Every option is an AI
            // estimate because ExploreX does not integrate a live booking API.
            const estimateChip = '<span class="badge bg-light text-dark border ms-2" data-testid="transit-estimate-badge" title="ExploreX has no live booking feed. Value shown is an AI estimate, not a confirmed price or seat."><i class="fas fa-hourglass-half me-1"></i>Estimated</span>';

            card.innerHTML = `
                ${recommendedBadge}
                <div class="d-flex flex-column flex-md-row w-100 gap-3 justify-content-between align-items-md-center">
                    <div class="d-flex align-items-center gap-3">
                        <div class="bg-primary text-white p-3 rounded-circle d-flex align-items-center justify-content-center" style="width:50px;height:50px;">
                            <i class="fas ${icon} fs-4"></i>
                        </div>
                        <div>
                            <h5 class="Bricolage mb-1 text-capitalize">${escapeHtml(type)} <small class="text-muted ms-2 fs-6">by ${escapeHtml(providerText)}</small></h5>
                            ${legText ? `<div class="fw-semibold small mb-1" data-testid="transit-leg"><i class="fas fa-arrow-right-long text-accent me-1"></i>${escapeHtml(legText)}${windowText ? ` <span class="fw-normal text-muted">· ${escapeHtml(windowText)}</span>` : ''}</div>` : ''}
                            <div class="text-secondary small d-flex gap-3 flex-wrap mt-2">
                                <span><i class="fas fa-clock me-1 text-accent"></i> ${escapeHtml(durationText)}</span>
                                <span><i class="fas fa-leaf ${ecoFriendlyClass} me-1"></i> ${escapeHtml(ecoFriendlyText)}</span>
                                <span><i class="fas fa-suitcase text-secondary me-1"></i> ${escapeHtml(baggageText)}</span>
                            </div>
                        </div>
                    </div>
                    <div class="text-md-end mt-3 mt-md-0 d-flex flex-md-column align-items-center align-items-md-end justify-content-between gap-2">
                        <div>${transitPriceBlock(opt, transitData, true)}${estimateChip}</div>
                        <button class="btn btn-sm ${isSelected ? 'btn-success' : 'btn-outline-primary'} transport-select-btn" data-testid="${testId}">${isSelected ? '<i class="fas fa-check"></i> Selected' : 'Select'}</button>
                    </div>
                </div>
            `;
            container.appendChild(card);

            card.querySelector('.transport-select-btn')?.addEventListener('click', () => {
                // Phase 5/8: the budget breakdown is computed once, on the server,
                // and shared by the state, the API and the email. Re-summing every
                // numeric field here produced a second, non-canonical total that
                // the budget page then contradicted. The selection is recorded and
                // the plan is flagged; the cost changes when the user regenerates.
                state.selectedTransportIdx = idx;
                state.selectedTransport = {
                    mode: type,
                    provider: opt.provider || null,
                    estimated_cost: opt.estimated_cost ?? opt.price_inr ?? null,
                    cost_basis: opt.cost_basis || null,
                };
                state.planIsStale = true;
                try {
                    sessionStorage.setItem('selectedTransportIdx', String(idx));
                    sessionStorage.setItem('selectedTransport', JSON.stringify(state.selectedTransport));
                } catch (e) { }
                renderTransport(transitData);
                showToast('Transport option selected. Regenerate your itinerary to apply the new cost.', 'info');
            });
        });

        // Requirement 4: also render local_transport (in-city options) if the
        // agent produced them. Users asked for a broader comparison — only
        // showing intercity made the section feel thin.
        if (localOptions.length > 0) renderLocalTransit(container, transitData, localOptions, typeIcons);
    }

    // In-city options live in their own renderer so the "no intercity leg" path
    // can still show them.
    function renderLocalTransit(container, transitData, localOptions, typeIcons) {
        const recommendedLocal = String(transitData.recommended_local_mode || '').toLowerCase();
        {
            const heading = document.createElement('h5');
            heading.className = 'Bricolage mt-4 mb-2';
            heading.innerHTML = '<i class="fas fa-city text-accent me-2"></i>Getting around ' +
                escapeHtml(String(transitData.destination || state.destination || 'the destination'));
            container.appendChild(heading);
            localOptions.forEach((lo, li) => {
                const mode = lo.mode || 'option';
                const icon = (typeIcons[String(mode).toLowerCase()] || 'fa-shoe-prints');
                const rec = String(mode).toLowerCase() === recommendedLocal ? '<span class="badge bg-warning text-dark ms-2">Recommended</span>' : '';
                const div = document.createElement('div');
                div.className = 'glass p-3 rounded-4 d-flex align-items-center justify-content-between mb-2';
                div.setAttribute('data-testid', 'transit-local-option');
                div.innerHTML = `
                    <div class="d-flex align-items-center gap-3">
                        <div class="bg-secondary text-white p-2 rounded-circle d-flex align-items-center justify-content-center" style="width:38px;height:38px;"><i class="fas ${icon}"></i></div>
                        <div>
                            <div class="fw-bold text-capitalize">${escapeHtml(String(mode))}${rec}</div>
                            <div class="text-secondary small">${escapeHtml(String(lo.recommendation_reason || ''))}</div>
                        </div>
                    </div>
                    <div class="text-end">${transitPriceBlock(lo, transitData, false)}
                        <span class="badge bg-light text-dark border ms-2" title="AI estimate, not live price"><i class="fas fa-hourglass-half me-1"></i>Estimated</span></div>
                `;
                container.appendChild(div);
            });
        }
    }

    // === Generate Itinerary (recommend step with the user's real selection) ===
    document.getElementById('btn-generate-route')?.addEventListener('click', () => {
        if (state.generating) return;
        if (state.selectedAttractionIds.size === 0) {
            alert('Please select at least one attraction to generate an itinerary.');
            return;
        }
        const ids = Array.from(state.selectedAttractionIds);
        const params = { selected_attraction_ids: JSON.stringify(ids), force_continue: 'true' };
        if (state.selectedAccommodationId) params.selected_accommodation_id = state.selectedAccommodationId;

        state.generating = true;
        const btn = document.getElementById('btn-generate-route');
        btn.disabled = true;
        btn.innerHTML = '<i class="fas fa-spinner fa-spin me-2"></i> Planning...';

        // Single call. The backend auto-loops recommend -> strategy -> communication
        // (pauses), then handleComplete drives communication -> route. No timers.
        triggerBackend('recommend', 'Here are my selected attractions', params);

        const restore = () => {
            btn.disabled = false;
            btn.innerHTML = 'Generate Itinerary <i class="fas fa-magic ms-2"></i>';
        };
        const poll = setInterval(() => { if (!state.generating) { clearInterval(poll); restore(); } }, 400);
        setTimeout(() => { clearInterval(poll); restore(); state.generating = false; }, 180000);
    });

    // === Send itinerary by email ===
    document.getElementById('btn-send-itinerary-email')?.addEventListener('click', async () => {
        const emailInput = document.getElementById('email-deliver-input');
        const notesInput = document.getElementById('email-deliver-notes');
        const email = emailInput ? emailInput.value.trim() : '';
        const notes = notesInput ? notesInput.value.trim() : '';
        const statusEl = document.getElementById('email-success-state');
        const errorEl = document.getElementById('email-error-message');
        const actionsEl = document.getElementById('email-action-buttons');
        
        if (errorEl) errorEl.classList.add('d-none');
        
        if (!email) {
            if (errorEl) { 
                errorEl.classList.remove('d-none');
                errorEl.innerHTML = '<i class="fas fa-exclamation-triangle me-1"></i> Please enter a valid email address.'; 
            }
            return;
        }
        
        // Basic email validation regex
        const emailRegex = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
        if (!emailRegex.test(email)) {
            if (errorEl) { 
                errorEl.classList.remove('d-none');
                errorEl.innerHTML = '<i class="fas fa-exclamation-triangle me-1"></i> Please enter a valid email address.'; 
            }
            return;
        }
        
        state.email = email;
        const btn = document.getElementById('btn-send-itinerary-email');
        btn.innerHTML = '<span class="spinner-border spinner-border-sm me-2" role="status" aria-hidden="true"></span> Sending...';
        btn.disabled = true;

        try {
            const response = await fetch('/api/email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ 
                    email: email,
                    notes: notes,
                    itinerary: state.itinerary,
                    budget: state.budget
                })
            });
            const data = await response.json();
            const ok = data.success === true || (data.success === undefined && data.status === 'success');
            if (response.ok && ok) {
                btn.innerHTML = 'Sent Successfully';
                if (statusEl) {
                    if (emailInput) emailInput.style.display = 'none';
                    if (notesInput) notesInput.style.display = 'none';
                    if (actionsEl) actionsEl.style.display = 'none';
                    if (errorEl) errorEl.style.display = 'none';
                    const noticeEl = document.getElementById('email-guest-notice');
                    if (noticeEl) noticeEl.style.display = 'none';
                    
                    statusEl.classList.remove('d-none'); 
                }
                showToast(`Itinerary sent to ${email}.`, 'success');
            } else {
                throw new Error(data.error || data.message || 'Email delivery failed.');
            }
        } catch (error) {
            btn.innerHTML = 'Send Itinerary';
            btn.disabled = false;
            if (errorEl) { 
                errorEl.classList.remove('d-none'); 
                errorEl.innerHTML = `<i class="fas fa-exclamation-triangle me-1"></i> ${escapeHtml(error.message)}`; 
            }
            showToast(error.message || 'Email delivery failed.', 'error');
        }
    });

    // === Boot ===
    // The 3D earth is decorative and must never be able to abort the auth boot
    // below. A WebGL/THREE failure here previously left the default-active
    // landing view on screen for signed-in users, trapping them in a
    // landing -> /login -> landing redirect loop.
    try {
        earthCtl = initEarth('landing-earth-canvas', 'landing-earth-container');
    } catch (e) {
        console.warn('Landing earth init failed; continuing boot.', e);
        earthCtl = null;
    }

    const auth = currentUser();
    const params = new URLSearchParams(window.location.search);
    const dest = params.get('dest') || (function () { try { return sessionStorage.getItem('explorex_dest'); } catch (e) { return null; } })();

    if (auth.authenticated) {
        enterApp(auth.userName);
        if (dest) {
            try { sessionStorage.removeItem('explorex_dest'); } catch (e) { }
            // Clear the ?dest= query from the address bar without reloading.
            window.history.replaceState({}, '', window.location.pathname);
            startTrip(dest);
        } else {
            // Refresh with no pending destination: pull the current trip back
            // from the backend so the session survives a page reload.
            restoreFromServer().then(hadTrip => {
                if (hadTrip && state.itinerary) switchTab('itinerary');
            });
        }
    } else {
        showLanding();
    }
    // ═════════════════════════════════════════════════════════════════════
    // Persistent AI Travel Assistant (Requirement 7)
    // ═════════════════════════════════════════════════════════════════════
    // One assistant rail that stays mounted across Explore, attractions,
    // Itinerary, Map, Weather, Budget and Transit. Every fact it shows comes
    // from the SERVER-side session state (/api/assistant) - switching tabs
    // never re-runs the planning pipeline and never invents a summary.
    // `var` (not `let`) on purpose: switchTab() can run from the boot code
    // above this block, and these bindings must already be readable by then.
    var assistantCache = null;
    var assistantCacheAt = 0;
    var assistantFetching = null;

    function assistantPrefOpen() {
        try {
            const v = localStorage.getItem('assistantOpen');
            return v === null ? true : v === '1';
        } catch (e) { return true; }
    }
    function assistantSetPrefOpen(open) {
        try { localStorage.setItem('assistantOpen', open ? '1' : '0'); } catch (e) { }
    }

    function updateAssistantVisibility() {
        const assistantPanel = document.getElementById('assistant-panel');
        const assistantOpenBtn = document.getElementById('assistant-panel-open');
        if (!assistantPanel) return;
        const onTripPage = state.currentTab && state.currentTab !== 'landing';
        const show = onTripPage && assistantPrefOpen();
        assistantPanel.classList.toggle('is-visible', !!show);
        document.body.classList.toggle('assistant-visible', !!show);
        // Off-canvas drawers need a dismiss affordance on tablet/phone widths.
        document.body.classList.toggle('assistant-backdrop', !!show && window.innerWidth < 1200);
        if (assistantOpenBtn) assistantOpenBtn.classList.toggle('d-none', !!show || !onTripPage);
    }

    function assistantRow(label, value) {
        if (value === null || value === undefined || value === '') return '';
        return `<div class="assistant-kv"><span>${escapeHtml(label)}</span><span>${escapeHtml(String(value))}</span></div>`;
    }

    function assistantRenderContext(ctx) {
        const assistantBody = document.getElementById('assistant-panel-body');
        if (!assistantBody) return;
        if (!ctx) { assistantBody.innerHTML = '<div class="assistant-state">No trip loaded yet.</div>'; return; }
        const trip = ctx.trip || {};
        let html = '';

        if (!ctx.has_trip) {
            html += `<div class="assistant-state">
                <i class="fas fa-mug-hot me-1" aria-hidden="true"></i>
                Tell the planner where you want to go and the assistant will keep track of it here.</div>`;
            assistantBody.innerHTML = html;
            return;
        }

        html += '<div class="assistant-section"><div class="assistant-section-title">Trip</div>';
        html += assistantRow('Destination', trip.destination || 'Not decided yet');
        html += assistantRow('Origin', trip.origin);
        html += assistantRow('Days', trip.days);
        html += assistantRow('Start date', trip.start_date);
        html += assistantRow('Travellers', trip.people);
        html += assistantRow('Budget', trip.budget_category || trip.budget_level);
        html += '</div>';

        const missing = ctx.missing_fields || [];
        if (missing.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Still needed</div>';
            html += missing.map(m => `<span class="assistant-chip is-warn">${escapeHtml(String(m))}</span>`).join('');
            html += '</div>';
        }

        const sel = ctx.selected_attractions || [];
        if (sel.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Selected attractions</div>';
            html += sel.slice(0, 8).map(a => `<span class="assistant-chip">${escapeHtml(a.name || a)}</span>`).join('');
            if (sel.length > 8) html += `<span class="assistant-chip">+${sel.length - 8} more</span>`;
            html += '</div>';
        }

        if (ctx.itinerary_days && ctx.itinerary_days.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Day-by-day</div><ul class="assistant-list">';
            ctx.itinerary_days.forEach(d => {
                const n = d.activity_count != null ? d.activity_count : (d.activities || []).length;
                html += `<li>Day ${escapeHtml(String(d.day))} — ${n} activit${n === 1 ? 'y' : 'ies'}${d.date ? ` · ${escapeHtml(String(d.date))}` : ''}</li>`;
            });
            html += '</ul></div>';
        }

        const unscheduled = ctx.unscheduled_attractions || [];
        if (unscheduled.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Not scheduled</div><ul class="assistant-list">';
            unscheduled.forEach(u => {
                html += `<li>${escapeHtml(String(u.name || 'Attraction'))} <span class="assistant-chip is-warn">${escapeHtml(String(u.reason || 'not placed'))}</span></li>`;
            });
            html += '</ul></div>';
        }

        // Only a stay the traveller actually picked is presented as chosen; the
        // first recommendation is labelled as a suggestion (never "booked").
        if (ctx.accommodation && ctx.accommodation.name) {
            const acc = ctx.accommodation;
            html += '<div class="assistant-section"><div class="assistant-section-title">Stay</div>';
            html += assistantRow('Selected', acc.name);
            if (acc.price_per_night) html += assistantRow('Nightly rate', `₹${Number(acc.price_per_night).toLocaleString('en-IN')}`);
            else if (acc.price_status === 'unavailable' || !acc.price_per_night) html += assistantRow('Nightly rate', 'Not reported');
            html += '</div>';
        }

        const b = ctx.budget || {};
        if (b && (b.total != null || b.status)) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Budget</div>';
            html += assistantRow('Estimated total', b.total_is_valid === false ? 'Incomplete data'
                : (b.total != null ? `₹${Math.round(b.total).toLocaleString('en-IN')}` : 'Not calculated'));
            if (b.budget_limit != null) html += assistantRow('Limit', `₹${Math.round(b.budget_limit).toLocaleString('en-IN')}`);
            if (b.status) html += assistantRow('Status', b.status === 'within' ? 'Within budget' : b.status === 'over' ? 'Over budget' : 'Incomplete data');
            html += '</div>';
        }

        const warns = ctx.budget_warnings || ctx.warnings || [];
        if (warns.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Watch out</div>';
            html += warns.map(w => `<div class="assistant-chip is-warn">${escapeHtml(String(w))}</div>`).join('');
            html += '</div>';
        }

        const tr = ctx.transit || {};
        if (tr.recommended_mode || tr.recommended_local_mode || tr.same_city) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Getting around</div>';
            if (tr.same_city) html += assistantRow('Intercity', 'None — origin and destination are the same city');
            else html += assistantRow('Intercity', (tr.intercity_options && tr.intercity_options[0] && (tr.intercity_options[0].mode || tr.intercity_options[0].name)) || tr.recommended_mode);
            html += assistantRow('Local', (tr.local_options && tr.local_options[0] && (tr.local_options[0].mode || tr.local_options[0].name)) || tr.recommended_local_mode);
            if (ctx.self_drive_recommended !== undefined && ctx.self_drive_recommended !== null) {
                html += assistantRow('Self-drive', ctx.self_drive_recommended ? 'Recommended' : 'Not needed');
            }
            html += '</div>';
        }

        if (ctx.email_sent) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Delivery</div>';
            html += assistantRow('Itinerary email', 'Sent');
            html += '</div>';
        }

        const ps = ctx.provider_status || {};
        const degraded = Object.entries(ps).filter(([, v]) => v && v.state && v.state !== 'ok');
        if (degraded.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Live data</div>';
            html += degraded.map(([k, v]) => `<span class="assistant-chip is-warn" title="${escapeHtml(String(v.detail || ''))}">${escapeHtml(k)}: ${escapeHtml(String(v.state))}</span>`).join('');
            html += '</div>';
        }

        const convo = (ctx.conversation || []).slice(-2);
        if (convo.length) {
            html += '<div class="assistant-section"><div class="assistant-section-title">Latest exchange</div>';
            convo.forEach(m => {
                const text = String(m.content || m.text || '').slice(0, 220);
                html += `<div class="assistant-kv"><span>${escapeHtml(m.role === 'user' ? 'You' : 'ExploreX')}</span><span>${escapeHtml(text)}</span></div>`;
            });
            html += '</div>';
        }

        assistantBody.innerHTML = html || '<div class="assistant-state">Nothing to show yet.</div>';
    }

    function refreshAssistant(force) {
        const assistantBody = document.getElementById('assistant-panel-body');
        if (!assistantBody) return Promise.resolve(null);
        const now = Date.now();
        if (!force && assistantCache && (now - assistantCacheAt) < 20000) {
            assistantRenderContext(assistantCache);
            return Promise.resolve(assistantCache);
        }
        if (assistantFetching) return assistantFetching;
        assistantBody.innerHTML = '<div class="assistant-state"><i class="fas fa-circle-notch fa-spin me-2" aria-hidden="true"></i>Loading your trip…</div>';
        assistantFetching = fetch('/api/assistant', { credentials: 'same-origin' })
            .then(r => r.ok ? r.json() : Promise.reject(new Error('HTTP ' + r.status)))
            .then(data => {
                const ctx = (data && data.context) || null;
                assistantCache = ctx; assistantCacheAt = Date.now();
                assistantRenderContext(ctx);
                return ctx;
            })
            .catch(err => {
                console.warn('assistant refresh failed', err);
                assistantBody.innerHTML = '<div class="assistant-state assistant-error">' +
                    'The assistant could not reach the trip data. <br>' +
                    '<button type="button" class="btn btn-sm btn-outline-secondary mt-2" id="assistant-retry-btn">Retry</button></div>';
                document.getElementById('assistant-retry-btn')?.addEventListener('click', () => refreshAssistant(true));
                return null;
            })
            .finally(() => { assistantFetching = null; });
        return assistantFetching;
    }

    document.getElementById('assistant-panel-collapse')?.addEventListener('click', () => {
        assistantSetPrefOpen(false);
        updateAssistantVisibility();
    });
    document.getElementById('assistant-panel-open')?.addEventListener('click', () => {
        assistantSetPrefOpen(true);
        updateAssistantVisibility();
        refreshAssistant(false);
    });
    document.getElementById('assistant-continue-chat')?.addEventListener('click', () => {
        // Follow-ups continue the SAME conversation - the chat step already
        // holds the collected information, so nothing is re-asked or overwritten.
        switchTab('chat');
        document.getElementById('chat-input-field')?.focus();
    });
    window.addEventListener('resize', updateAssistantVisibility);
    document.addEventListener('click', e => {
        // Close the mobile drawer when tapping outside of it.
        const assistantPanel = document.getElementById('assistant-panel');
        const assistantOpenBtn = document.getElementById('assistant-panel-open');
        if (!assistantPanel || window.innerWidth >= 1200) return;
        if (!assistantPrefOpen()) return;
        if (!assistantPanel.contains(e.target) && e.target !== assistantOpenBtn && !assistantOpenBtn?.contains(e.target)) {
            document.body.classList.remove('assistant-backdrop');
            assistantPanel.classList.remove('is-visible');
        }
    });

    if (state.currentTab && state.currentTab !== 'landing') { updateAssistantVisibility(); refreshAssistant(false); }
    syncEarth();
});

window.sendEmailItinerary = function () {
    const modalEl = document.getElementById('emailModal');
    if (modalEl) {
        const auth = window.EXPLOREX || {};
        const input = document.getElementById('email-deliver-input');
        const notice = document.getElementById('email-guest-notice');
        const sendBtn = document.getElementById('btn-send-itinerary-email');
        const statusEl = document.getElementById('email-success-state');
        if (statusEl) { statusEl.classList.add('d-none'); statusEl.textContent = ''; }
        if (input && !input.value && auth.userEmail && auth.userEmail.indexOf('guest@') !== 0) {
            input.value = auth.userEmail;
        }
        if (notice) notice.classList.toggle('d-none', !auth.isGuest);
        if (sendBtn) sendBtn.disabled = !!auth.isGuest;
        const modal = bootstrap.Modal.getInstance(modalEl) || new bootstrap.Modal(modalEl);
        modal.show();
    } else {
        // Email modal not found
    }
};

// window.addEventListener('load', () => { setTimeout(fetchDestinations, 500); });
