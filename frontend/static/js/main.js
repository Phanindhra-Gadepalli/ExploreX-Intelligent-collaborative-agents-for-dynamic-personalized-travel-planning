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
        // map layers
        map: null,
        mapMarkers: [],
        routePolylines: [],
        mapCurrentDay: 'all',
        activeItineraryDay: sessionStorage.getItem('activeItineraryDay') || 1,
        accTier: 'all',
        foodCat: 'all',
        selectedTransportIdx: null
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
        state.accTier = 'all';
        state.foodCat = 'all';
        state.selectedTransportIdx = null;

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
        if (data.itinerary) {
            state.itinerary = data.itinerary;
            renderItinerary(data.itinerary);
            renderMap(data.itinerary);
            renderFood(state.food);
        }
        if (data.budget) { state.budget = data.budget; renderBudget(data.budget); }
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
    }

    // === Render Logic ===
    function fieldScore(a) {
        const raw = a.score ?? a.match_percentage ?? a._internal_score ?? a.match_score;
        const n = typeof raw === 'number' ? raw : parseFloat(raw);
        if (isNaN(n)) return 85;
        return n > 1 ? Math.round(n) : Math.round(n * 100);
    }
    function fieldDuration(a) { return a.duration || a.estimated_duration || a.visit_duration || '2h'; }
    function fieldAddress(a) { return a.address || a.location_name || a.category || 'Location'; }
    function fieldRating(a) { return a.rating ?? a.stars ?? 4.5; }
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
                        <div class="match-badge"><i class="fas fa-check-circle"></i> ${fieldScore(a)}% Match</div>
                    </div>
                    <p class="text-muted small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(fieldAddress(a))}</p>
                    <div class="d-flex justify-content-between text-secondary small mb-2">
                        <span><i class="fas fa-star text-warning me-1"></i> ${fieldRating(a)}</span>
                        <span><i class="fas fa-clock me-1"></i> ${escapeHtml(fieldDuration(a))}</span>
                    </div>
                    <div class="d-flex justify-content-between text-secondary small mb-2">
                        <span><i class="fas fa-ticket-alt text-info me-1"></i> ${escapeHtml(a.entry_fee || a.price || 'Free')}</span>
                        <span><i class="fas fa-headphones text-primary me-1"></i> ${a.audio_guide || a.metadata?.audio_guide ? 'Audio Guide' : 'No Audio'}</span>
                    </div>
                    <p class="text-muted small mb-0">${escapeHtml(a.description || 'An amazing attraction to visit.').substring(0, 80)}${(a.description || 'An amazing attraction to visit.').length > 80 ? '...' : ''}</p>
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
        document.getElementById('modal-rating').innerText = fieldRating(a);
        document.getElementById('modal-desc').innerText = a.description || 'No description available.';
        
        const durationEl = document.getElementById('modal-duration');
        if (durationEl) durationEl.innerText = fieldDuration(a);
        const feeEl = document.getElementById('modal-fee');
        if (feeEl) {
            let price = a.price === 0 || a.price_level === 0 ? 'Free' : (a.entry_fee || a.price || 'Free');
            feeEl.innerText = String(price);
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
                        <i class="fas fa-star text-warning me-1"></i> <span class="fw-bold me-2">${escapeHtml(acc.rating || '4.0')}</span>
                    </div>
                    <p class="text-secondary small mb-3 text-truncate">${escapeHtml((acc.amenities || []).join(' · ') || 'Free WiFi · Air Conditioning')}</p>
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
                    if (state.budget && state.budget.accommodation !== undefined && acc.price) {
                        const oldAcc = state.accommodations.find(a => a.id === state.selectedAccommodationId);
                        if (oldAcc && oldAcc.price) state.budget.accommodation -= (oldAcc.price * (state.days || 1));
                        state.budget.accommodation += (acc.price * (state.days || 1));
                        state.budget.total_cost = Object.entries(state.budget).filter(([k,v]) => typeof v === 'number' && k !== 'total_cost').reduce((a, [,v])=>a+v,0);
                        renderBudget(state.budget);
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
                    document.getElementById('modal-rating').innerText = acc.rating || '4.0';
                    
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
            
            // Generate price string (e.g. 2 -> ₹₹)
            const priceLevel = parseInt(s.price_level, 10) || 2;
            const priceStr = '₹'.repeat(priceLevel);
            
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
                        <i class="fas fa-star text-warning me-1"></i> <span class="fw-bold me-2">${escapeHtml(s.rating || '4.2')}</span>
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

        const tl = document.getElementById('itinerary-timeline');
        const pills = document.getElementById('itinerary-day-pills');
        const title = document.getElementById('itinerary-title');
        if (!tl) return;
        tl.innerHTML = '';
        if (pills) pills.innerHTML = '';
        if (title) title.innerText = `${daysArray.length}-Day Itinerary`;

        if (!daysArray || daysArray.length === 0) return;

        // Initialize active day if not set or out of bounds
        let activeDayRaw = sessionStorage.getItem('activeItineraryDay');
        if (activeDayRaw) state.activeItineraryDay = activeDayRaw === 'all' ? 'all' : parseInt(activeDayRaw, 10);
        else if (!state.activeItineraryDay || state.activeItineraryDay === 'all') state.activeItineraryDay = 1;
        
        if (state.activeItineraryDay !== 'all' && !daysArray.some(d => d.day === state.activeItineraryDay)) {
            state.activeItineraryDay = daysArray[0].day;
            sessionStorage.setItem('activeItineraryDay', state.activeItineraryDay);
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
                    sessionStorage.setItem('activeItineraryDay', day.day);
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
        if (!state.map || daysArray.length === 0) return;

        const filtersContainer = document.getElementById('map-day-filters');
        if (filtersContainer) {
            filtersContainer.innerHTML = '';
            const btnAll = document.createElement('button');
            btnAll.className = `btn btn-sm rounded-pill flex-shrink-0 ${state.activeItineraryDay === 'all' ? 'btn-primary' : 'btn-outline-secondary'}`;
            btnAll.innerText = 'All Days';
            btnAll.onclick = () => { 
                state.activeItineraryDay = 'all'; 
                sessionStorage.setItem('activeItineraryDay', 'all');
                renderMap(state.itinerary); 
                renderItinerary(state.itinerary);
            };
            filtersContainer.appendChild(btnAll);

            daysArray.forEach(day => {
                const btn = document.createElement('button');
                btn.className = `btn btn-sm rounded-pill flex-shrink-0 ${state.activeItineraryDay === day.day ? 'btn-success' : 'btn-outline-secondary'}`;
                btn.innerText = `Day ${day.day}`;
                btn.onclick = () => { 
                    state.activeItineraryDay = day.day; 
                    sessionStorage.setItem('activeItineraryDay', day.day);
                    renderMap(state.itinerary); 
                    renderItinerary(state.itinerary);
                };
                filtersContainer.appendChild(btn);
            });
        }

        const mapTl = document.getElementById('map-timeline');
        if (mapTl) mapTl.innerHTML = '';

        state.mapMarkers.forEach(m => state.map.removeLayer(m));
        state.routePolylines.forEach(p => state.map.removeLayer(p));
        state.mapMarkers = [];
        state.routePolylines = [];

        let allPoints = [];
        let totalDistanceMeters = 0;
        let totalDurationSeconds = 0;
        let totalStops = 0;
        let anyRoutingFailed = false;

        const daysToRender = state.activeItineraryDay === 'all' ? daysArray : daysArray.filter(d => d.day === state.activeItineraryDay);

        let locationCounts = {};

        for (const day of daysToRender) {
            let dayPoints = [];
            if (mapTl) {
                const dayHeader = document.createElement('div');
                dayHeader.innerHTML = `<h5 class="Bricolage mt-3 mb-3 text-accent">Day ${day.day}</h5>`;
                mapTl.appendChild(dayHeader);
            }

            let markerIndex = 1;
            (day.activities || []).forEach((spot, spotIdx) => {
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

            if (dayPoints.length > 1) {
                const routeData = await fetchOSRMRoute(dayPoints);
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
        const currency = currencySelect ? currencySelect.value : 'INR';
        const rates = { INR: 1, USD: 0.012, EUR: 0.011 }; // Mock rates
        const rate = rates[currency] || 1;
        const sym = currency === 'USD' ? '$' : currency === 'EUR' ? '€' : '₹';

        if (currencySelect && !currencySelect.dataset.listener) {
            currencySelect.dataset.listener = 'true';
            currencySelect.addEventListener('change', () => {
                if (state.budget) renderBudget(state.budget);
            });
        }

        const totalEl = document.getElementById('total-budget-display');
        const total = budget.total ?? budget.total_cost ?? budget.estimated_total ?? 0;
        const convertedTotal = total * rate;
        if (totalEl) totalEl.innerText = `${sym}${Math.round(convertedTotal).toLocaleString('en-US')}`;

        const allowedCategories = ['accommodation', 'food', 'intercity_transport', 'local_transport', 'attractions', 'car_rental', 'fuel', 'miscellaneous'];
        
        const colors = {
            accommodation: '#0284C7', food: '#0EA5E9', local_transport: '#38BDF8', 
            attractions: '#059669', car_rental: '#7C3AED', fuel: '#DB2777',
            intercity_transport: '#0891B2', miscellaneous: '#D97706'
        };

        const entries = Object.entries(budget).filter(([k, v]) => allowedCategories.includes(k) && typeof v === 'number' && v > 0);
        const sum = entries.reduce((acc, [, v]) => acc + v, 0) || 1;

        let summaryHtml = '';
        const limitAmt = budget.budget_amount || budget.budget_limit;
        if (limitAmt) {
            const limitConverted = limitAmt * rate;
            const limitLabel = `<div class="d-flex justify-content-between mb-2 pb-2 border-bottom"><span class="fw-bold text-dark" data-testid="budget-limit">Budget Limit:</span> <span class="fw-bold">${sym}${Math.round(limitConverted).toLocaleString('en-US')}</span></div>`;
            
            let statusHtml = '';
            const remainingAmt = (budget.remaining != null) ? budget.remaining : (budget.remaining_budget != null ? budget.remaining_budget : null);
            const remainingConverted = remainingAmt != null ? Math.abs(remainingAmt) * rate : 0;
            if (remainingAmt != null && remainingAmt >= 0) {
                statusHtml = `<div class="d-flex justify-content-between mb-2"><span class="text-success fw-bold" data-testid="budget-remaining">Remaining:</span> <span class="text-success fw-bold">${sym}${Math.round(remainingConverted).toLocaleString('en-US')}</span></div>`;
            } else if (remainingAmt != null) {
                statusHtml = `<div class="d-flex justify-content-between mb-2"><span class="text-danger fw-bold" data-testid="budget-remaining">Over budget:</span> <span class="text-danger fw-bold">${sym}${Math.round(remainingConverted).toLocaleString('en-US')}</span></div>`;
            }
            
            const pct = budget.utilization_percentage || Math.round((sum / limitAmt) * 100);
            const pctHtml = `<div class="d-flex justify-content-between mb-4 pb-2 border-bottom"><span class="text-secondary small">Utilization:</span> <span class="text-secondary small">${pct}% used</span></div>`;
            
            summaryHtml = limitLabel + statusHtml + pctHtml;
        }

        const legendHtml = entries.map(([k, v]) => {
            const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const color = colors[k.toLowerCase()] || '#94A3B8';
            const pct = Math.round((v / sum) * 100);
            const convertedV = v * rate;
            return `
                <div class="legend-item d-flex justify-content-between align-items-center mb-2" data-testid="budget-category">
                    <div><span class="legend-color rounded-circle d-inline-block me-2 shadow-sm" style="background: ${color}; width: 12px; height: 12px;"></span> <span class="fw-bold">${escapeHtml(label)}</span></div>
                    <div class="text-secondary">${sym}${Math.round(convertedV).toLocaleString('en-US')} · ${pct}%</div>
                </div>
            `;
        }).join('');
        
        container.innerHTML = summaryHtml + legendHtml;

        const canvas = document.getElementById('budget-donut-chart');
        if (canvas && entries.length) {
            if (window.budgetChartInstance) {
                window.budgetChartInstance.destroy();
            }

            const chartData = entries.map(([k, v]) => ({
                label: k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase()),
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

        const options = Array.isArray(transitData.options) ? transitData.options
            : (Array.isArray(transitData.intercity_transport) ? transitData.intercity_transport : []);
        const recommendedMode = String(transitData.recommended_mode || '').toLowerCase();

        if (options.length === 0) {
            container.innerHTML = `
                <div class="empty-state-card">
                    <i class="fas fa-bus"></i>
                    <h5 class="Bricolage">No transport options available</h5>
                    <p>We couldn't find any transit options from your origin.</p>
                </div>
            `;
            return;
        }

        const typeIcons = { flight: 'fa-plane', train: 'fa-train', bus: 'fa-bus', car: 'fa-car', driving: 'fa-car' };

        // Sort options to put the recommended mode first
        const sortedOptions = [...options].sort((a, b) => {
            const aIsRec = a.mode && a.mode.toLowerCase() === recommendedMode;
            const bIsRec = b.mode && b.mode.toLowerCase() === recommendedMode;
            if (aIsRec && !bIsRec) return -1;
            if (!aIsRec && bIsRec) return 1;
            return 0;
        });

        sortedOptions.forEach((opt, idx) => {
            const type = opt.mode || 'route';
            const icon = typeIcons[String(type).toLowerCase()] || 'fa-route';
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

            const costText = opt.estimated_cost ? '₹' + String(opt.estimated_cost) : (opt.price_inr ? '₹' + String(opt.price_inr) : 'Live price unavailable');
            const durationText = opt.duration || 'Unknown duration';
            const providerText = opt.provider || 'Provider unknown';
            const baggageText = opt.baggage || 'Baggage info unavailable';
            const ecoFriendlyText = opt.eco_friendly ? 'Eco-friendly' : 'Standard emissions';
            const ecoFriendlyClass = opt.eco_friendly ? 'text-success' : 'text-secondary';

            card.innerHTML = `
                ${recommendedBadge}
                <div class="d-flex flex-column flex-md-row w-100 gap-3 justify-content-between align-items-md-center">
                    <div class="d-flex align-items-center gap-3">
                        <div class="bg-primary text-white p-3 rounded-circle d-flex align-items-center justify-content-center" style="width:50px;height:50px;">
                            <i class="fas ${icon} fs-4"></i>
                        </div>
                        <div>
                            <h5 class="Bricolage mb-1 text-capitalize">${escapeHtml(type)} <small class="text-muted ms-2 fs-6">by ${escapeHtml(providerText)}</small></h5>
                            <div class="text-secondary small d-flex gap-3 flex-wrap mt-2">
                                <span><i class="fas fa-clock me-1 text-accent"></i> ${escapeHtml(durationText)}</span>
                                <span><i class="fas fa-leaf ${ecoFriendlyClass} me-1"></i> ${escapeHtml(ecoFriendlyText)}</span>
                                <span><i class="fas fa-suitcase text-secondary me-1"></i> ${escapeHtml(baggageText)}</span>
                            </div>
                        </div>
                    </div>
                    <div class="text-md-end mt-3 mt-md-0 d-flex flex-md-column align-items-center align-items-md-end justify-content-between gap-2">
                        <div class="fw-bold fs-5 text-accent">${escapeHtml(costText)}</div>
                        <button class="btn btn-sm ${isSelected ? 'btn-success' : 'btn-outline-primary'} transport-select-btn" data-testid="${testId}">${isSelected ? '<i class="fas fa-check"></i> Booked' : 'Select'}</button>
                    </div>
                </div>
            `;
            container.appendChild(card);

            card.querySelector('.transport-select-btn')?.addEventListener('click', () => {
                if (state.budget && state.budget.intercity_transport !== undefined) {
                    const oldOpt = sortedOptions[state.selectedTransportIdx];
                    const numPeople = Math.max(1, parseInt(state.travelers?.people || 1));
                    if (oldOpt && oldOpt.estimated_cost) state.budget.intercity_transport -= (oldOpt.estimated_cost * 2 * numPeople);
                    if (opt.estimated_cost) state.budget.intercity_transport += (opt.estimated_cost * 2 * numPeople);
                    
                    // recalculate total
                    let total = 0;
                    for (let k in state.budget) {
                        if (k !== 'total' && k !== 'total_cost' && typeof state.budget[k] === 'number') {
                            total += state.budget[k];
                        }
                    }
                    state.budget.total = total;
                    state.budget.total_cost = total;
                    renderBudget(state.budget);
                }
                state.selectedTransportIdx = idx;
                renderTransport(transitData);
            });
        });
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
    earthCtl = initEarth('landing-earth-canvas', 'landing-earth-container');

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
