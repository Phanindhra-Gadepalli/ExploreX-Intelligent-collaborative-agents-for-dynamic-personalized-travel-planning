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
        routePolylines: []
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

        if (tabId === 'map' && state.map) setTimeout(() => state.map.invalidateSize(), 100);
        syncEarth();
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
        });
    });

    if (interestWeight && weightVal) {
        interestWeight.addEventListener('input', e => {
            state.interestWeight = parseInt(e.target.value, 10) || 0;
            weightVal.innerText = state.interestWeight + '%';
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
        if (state.weather.length) renderWeather(state.weather);
        if (state.itinerary) {
            renderItinerary(state.itinerary);
            renderMap(state.itinerary);
            renderFoodFromItinerary(state.itinerary);
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
            state.weather = Array.isArray(t.weather) ? t.weather : [];
            state.itinerary = (Array.isArray(t.itinerary) && t.itinerary.length) ? t.itinerary : null;
            state.budget = (t.budget && Object.keys(t.budget).length) ? t.budget : null;
            state.transport = t.transit_options || [];
            state.selectedAttractionIds = new Set(
                (Array.isArray(t.selected_attractions) ? t.selected_attractions : [])
                    .map(a => a && (a.id || a.attraction_id)).filter(Boolean)
            );
            renderAll();
            return !!(state.itinerary || state.attractions.length);
        } catch (e) {
            console.error('Restore error:', e);
            return false;
        }
    }

    document.getElementById('reset-btn')?.addEventListener('click', async () => {
        if (!confirm('Start a new trip? This clears all current progress.')) return;
        try { await fetch('/api/reset', { method: 'POST', credentials: 'same-origin' }); } catch (e) { console.error('Reset error:', e); }
        resetTrip();
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
            console.error('Logout error:', e);
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
    function triggerBackend(step, userInput, extraParams = {}) {
        let url = `/api/stream?step=${step}&user_input=${encodeURIComponent(userInput)}`;
        if (state.sessionId) url += `&session_id=${state.sessionId}`;
        for (const [k, v] of Object.entries(extraParams)) url += `&${k}=${encodeURIComponent(v)}`;

        const msgDiv = addChatMessage('', 'ai');
        if (msgDiv) msgDiv.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Processing...';

        // Give the Explore view instant feedback while a trip is being planned.
        if (step === 'chat') showRecommendationSkeletons();

        const eventSource = new EventSource(url);
        let fullText = '';

        eventSource.onmessage = e => {
            let data;
            try { data = JSON.parse(e.data); } catch (err) { return; }

            if (data.type === 'error') {
                if (msgDiv) msgDiv.innerHTML = `<span class="text-danger"><i class="fas fa-exclamation-circle"></i> Error: ${data.error}</span>`;
                showToast(data.error || 'Something went wrong.', 'error');
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

            if (data.type !== 'complete') return;

            eventSource.close();
            if (data.session_id) state.sessionId = data.session_id;
            if (msgDiv && fullText.trim() === '') msgDiv.innerHTML = 'Got it! Preparing the next steps.';
            handleComplete(data);
        };

        eventSource.onerror = () => {
            eventSource.close();
            state.generating = false;
            clearRecommendationSkeletons();
            if (msgDiv && !fullText) msgDiv.innerHTML = `<span class="text-danger">Connection lost.</span>`;
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
        }

        // Recommendations ready -> render + let the user choose.
        if (Array.isArray(data.recommended_attractions) && data.recommended_attractions.length) {
            state.attractions = data.recommended_attractions;
            renderRecommendations(state.attractions);
            if (Array.isArray(data.accommodations)) {
                state.accommodations = data.accommodations;
                renderAccommodations(state.accommodations);
            }
            switchTab('explore');
            showToast(`${state.attractions.length} places curated for you — select your favorites.`, 'success');
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
            renderFoodFromItinerary(data.itinerary);
        }
        if (data.budget) { state.budget = data.budget; renderBudget(data.budget); }
        if (data.state && data.state.weather_forecast) {
            state.weather = data.state.weather_forecast;
            renderWeather(data.state.weather_forecast);
        }
        if (data.transit_options) { state.transport = data.transit_options; renderTransport(data.transit_options); }

        if (data.itinerary) {
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
        return (img && String(img).startsWith('http')) ? img : 'https://images.unsplash.com/photo-1469854523086-cc02fe5d8800';
    }
    function escapeHtml(s) {
        return String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    }

    function renderRecommendations(attrs) {
        const container = document.getElementById('recommendations-container');
        if (!container) return;
        container.innerHTML = '';

        attrs.forEach((a, idx) => {
            if (!a) return;
            const id = a.id || `attr-${idx}`;
            const div = document.createElement('div');
            div.className = 'bento-card';

            const selected = state.selectedAttractionIds.has(id);
            div.innerHTML = `
                <img src="${escapeHtml(fieldImage(a))}" class="bento-img" onerror="this.style.display='none'">
                <div class="bento-content">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <h5 class="Bricolage mb-0">${escapeHtml(a.name || 'Attraction')}</h5>
                        <div class="match-badge"><i class="fas fa-check-circle"></i> ${fieldScore(a)}% Match</div>
                    </div>
                    <p class="text-muted small mb-3"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(fieldAddress(a))}</p>
                    <div class="d-flex justify-content-between text-secondary small">
                        <span><i class="fas fa-star text-warning me-1"></i> ${fieldRating(a)}</span>
                        <span><i class="fas fa-clock me-1"></i> ${escapeHtml(fieldDuration(a))}</span>
                    </div>
                </div>
                <div class="p-3 border-top d-flex justify-content-between align-items-center" style="background: var(--bg-alt); font-size: 0.9rem;">
                    <span class="text-accent fw-bold explore-link" style="cursor:pointer;">Explore <i class="fas fa-arrow-right ms-1"></i></span>
                    <button class="btn btn-sm ${selected ? 'btn-success' : 'btn-outline-primary'} toggle-select-btn" data-id="${escapeHtml(id)}">
                        ${selected ? '<i class="fas fa-check"></i> Selected' : 'Select'}
                    </button>
                </div>
            `;
            container.appendChild(div);

            div.querySelector('.explore-link')?.addEventListener('click', e => { e.stopPropagation(); openAttractionModal(a); });
            div.querySelector('.bento-content')?.addEventListener('click', () => openAttractionModal(a));
            div.querySelector('.bento-img')?.addEventListener('click', () => openAttractionModal(a));

            const toggleBtn = div.querySelector('.toggle-select-btn');
            toggleBtn.addEventListener('click', e => { e.stopPropagation(); toggleSelect(a, id, toggleBtn); });
        });
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

    function openAttractionModal(a) {
        const id = a.id || '';
        document.getElementById('modal-title').innerText = a.name || 'Attraction';
        document.getElementById('modal-location').innerText = fieldAddress(a);
        document.getElementById('modal-rating').innerText = fieldRating(a);
        document.getElementById('modal-desc').innerText = a.description || 'No description available.';
        const img = document.getElementById('modal-image');
        img.src = fieldImage(a);
        img.onerror = () => { img.style.display = 'none'; };

        const addBtn = document.getElementById('modal-add-btn');
        if (addBtn) {
            const sync = () => {
                addBtn.innerHTML = state.selectedAttractionIds.has(id)
                    ? '<i class="fas fa-minus"></i> Remove from Itinerary'
                    : '<i class="fas fa-plus"></i> Add to Itinerary';
            };
            sync();
            addBtn.onclick = () => {
                const cardBtn = document.querySelector(`#recommendations-container .toggle-select-btn[data-id="${CSS.escape(id)}"]`);
                toggleSelect(a, id, cardBtn);
                sync();
            };
        }
        new bootstrap.Modal(document.getElementById('attractionModal')).show();
    }

    function renderAccommodations(accs) {
        const container = document.getElementById('accommodation-options');
        if (!container) return;
        if (!Array.isArray(accs) || accs.length === 0) {
            container.innerHTML = '<p class="text-muted w-100 text-center py-4">No accommodations found for this trip yet.</p>';
            return;
        }
        container.innerHTML = '';
        accs.forEach(acc => {
            if (!acc) return;
            const card = document.createElement('div');
            card.className = 'bento-card';
            const selected = state.selectedAccommodationId === acc.id;
            card.innerHTML = `
                <img src="${escapeHtml(fieldImage(acc))}" class="bento-img" onerror="this.style.display='none'">
                <div class="bento-content">
                    <h5 class="Bricolage mb-1">${escapeHtml(acc.name || 'Stay')}</h5>
                    <p class="text-muted small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${escapeHtml(acc.address || acc.location_name || 'Local stay')}</p>
                    <div class="d-flex justify-content-between align-items-center">
                        <span class="fw-bold text-accent">${escapeHtml(acc.price ? String(acc.price) : 'Price on request')}</span>
                        <button class="btn btn-sm ${selected ? 'btn-success' : 'btn-outline-primary'} acc-select-btn">${selected ? '<i class="fas fa-check"></i> Selected' : 'Select'}</button>
                    </div>
                </div>
            `;
            container.appendChild(card);
            card.querySelector('.acc-select-btn')?.addEventListener('click', e => {
                e.stopPropagation();
                state.selectedAccommodationId = acc.id;
                renderAccommodations(accs);
            });
        });
    }

    function renderFoodFromItinerary(itineraryData) {
        const container = document.getElementById('food-options');
        if (!container) return;
        const foods = [];
        (itineraryData || []).forEach(day => {
            (day.spots || []).forEach(s => {
                const cat = String(s.category || '').toLowerCase();
                if (cat.includes('food') || cat.includes('restaurant') || cat.includes('cafe') || cat.includes('dining')) foods.push(s);
            });
        });
        state.food = foods;
        if (foods.length === 0) {
            container.innerHTML = '<p class="text-muted w-100 text-center py-4">No dining spots were added to this itinerary.</p>';
            return;
        }
        container.innerHTML = '';
        foods.forEach(s => {
            const card = document.createElement('div');
            card.className = 'bento-card';
            card.innerHTML = `
                <img src="${escapeHtml(fieldImage(s))}" class="bento-img" onerror="this.style.display='none'">
                <div class="bento-content">
                    <h5 class="Bricolage mb-1">${escapeHtml(s.name || 'Eatery')}</h5>
                    <p class="text-muted small mb-0"><i class="fas fa-clock text-accent me-1"></i> ${escapeHtml(s.start_time || '')} · ${escapeHtml(s.category || 'Food')}</p>
                </div>
            `;
            container.appendChild(card);
        });
    }

    function renderItinerary(itineraryData) {
        const tl = document.getElementById('itinerary-timeline');
        const mapTl = document.getElementById('map-timeline');
        const pills = document.getElementById('itinerary-day-pills');
        const title = document.getElementById('itinerary-title');
        if (!tl) return;
        tl.innerHTML = '';
        if (mapTl) mapTl.innerHTML = '';
        if (pills) pills.innerHTML = '';
        if (title) title.innerText = `${itineraryData.length}-Day Itinerary`;

        itineraryData.forEach((day, i) => {
            if (pills) {
                const pill = document.createElement('span');
                pill.className = `badge ${i === 0 ? 'bg-primary' : 'bg-light text-dark border'} rounded-pill py-2 px-3`;
                pill.innerText = `Day ${day.day}`;
                pills.appendChild(pill);
            }

            const dayDiv = document.createElement('div');
            dayDiv.id = `itinerary-day-${day.day}`;
            dayDiv.setAttribute('data-testid', `itinerary-day-${day.day}`);
            dayDiv.innerHTML = `<h4 class="Bricolage mt-5 mb-4 text-accent"><i class="fas fa-sun me-2"></i> Day ${day.day} <span class="text-secondary ms-2" style="font-size: 1.1rem; font-weight: normal;">${day.date || ''}</span></h4>`;
            tl.appendChild(dayDiv);

            (day.spots || []).forEach(spot => {
                const item = document.createElement('div');
                item.className = 'timeline-item';
                item.innerHTML = `
                    <div class="timeline-dot"></div>
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
                tl.appendChild(item);

                if (mapTl && spot.category !== 'Food') {
                    const mItem = item.cloneNode(true);
                    mItem.querySelector('.timeline-img')?.remove();
                    const mc = mItem.querySelector('.timeline-content');
                    if (mc) mc.style.padding = '1rem';
                    mapTl.appendChild(mItem);
                }
            });
        });
    }

    function haversine(a, b) {
        const R = 6371;
        const dLat = (b[0] - a[0]) * Math.PI / 180;
        const dLng = (b[1] - a[1]) * Math.PI / 180;
        const la1 = a[0] * Math.PI / 180, la2 = b[0] * Math.PI / 180;
        const h = Math.sin(dLat / 2) ** 2 + Math.cos(la1) * Math.cos(la2) * Math.sin(dLng / 2) ** 2;
        return 2 * R * Math.asin(Math.sqrt(h));
    }

    function renderMap(itineraryData) {
        if (!state.map) initMap();
        if (!state.map) return;

        state.mapMarkers.forEach(m => state.map.removeLayer(m));
        state.routePolylines.forEach(p => state.map.removeLayer(p));
        state.mapMarkers = [];
        state.routePolylines = [];

        let allPoints = [];
        let totalDistance = 0;
        let totalStops = 0;

        itineraryData.forEach(day => {
            let dayPoints = [];
            (day.spots || []).forEach(s => {
                const lat = s.location && (s.location.lat ?? s.location.latitude);
                const lng = s.location && (s.location.lng ?? s.location.longitude);
                if (lat != null && lng != null && !isNaN(lat) && !isNaN(lng)) {
                    const pt = [Number(lat), Number(lng)];
                    dayPoints.push(pt);
                    allPoints.push(pt);
                    totalStops++;

                    const m = L.circleMarker(pt, { radius: 8, fillColor: '#0284C7', color: '#fff', weight: 2, fillOpacity: 1 }).addTo(state.map);
                    m.on('click', () => {
                        document.getElementById('map-timeline')?.classList.add('d-none');
                        document.getElementById('map-location-details')?.classList.remove('d-none');
                        const t = document.getElementById('map-detail-title'); if (t) t.innerText = s.name || '';
                        const d = document.getElementById('map-detail-desc'); if (d) d.innerText = s.description || s.category || 'No details available.';
                        const im = document.getElementById('map-detail-img'); if (im) { im.src = fieldImage(s); im.style.display = ''; im.onerror = () => { im.style.display = 'none'; }; }
                    });
                    state.mapMarkers.push(m);
                }
            });
            if (dayPoints.length > 1) {
                for (let i = 1; i < dayPoints.length; i++) totalDistance += haversine(dayPoints[i - 1], dayPoints[i]);
                const line = L.polyline(dayPoints, { color: '#0EA5E9', weight: 4, dashArray: '5, 10' }).addTo(state.map);
                state.routePolylines.push(line);
            }
        });

        const distEl = document.getElementById('map-total-distance');
        if (distEl) distEl.innerText = totalStops > 1 ? `${totalDistance.toFixed(1)} km` : '—';
        const stopsEl = document.getElementById('map-total-stops');
        if (stopsEl) stopsEl.innerText = totalStops || '—';

        if (allPoints.length > 0) {
            state.map.fitBounds(L.latLngBounds(allPoints), { padding: [50, 50] });
        }
    }

    function renderBudget(budget) {
        const totalEl = document.getElementById('total-budget-display');
        const total = budget.total ?? budget.total_cost ?? 0;
        if (totalEl) totalEl.innerText = `₹${Math.round(total).toLocaleString('en-IN')}`;

        const container = document.getElementById('budget-breakdown');
        if (!container) return;

        const colors = {
            accommodation: '#0284C7', food: '#0EA5E9', transport: '#38BDF8',
            attractions: '#059669', car_rental: '#7C3AED', fuel_cost: '#DB2777',
            intercity_transport: '#0891B2', miscellaneous: '#D97706', contingency: '#D97706'
        };

        const entries = Object.entries(budget).filter(([k, v]) => k !== 'total' && k !== 'total_cost' && typeof v === 'number' && v > 0);
        const sum = entries.reduce((acc, [, v]) => acc + v, 0) || 1;

        container.innerHTML = entries.map(([k, v]) => {
            const label = k.replace(/_/g, ' ').replace(/\b\w/g, c => c.toUpperCase());
            const color = colors[k] || '#94A3B8';
            const pct = Math.round((v / sum) * 100);
            return `
                <div class="legend-item">
                    <div><span class="legend-color" style="background: ${color}"></span> <span class="fw-bold">${label}</span></div>
                    <div class="text-secondary">₹${Math.round(v).toLocaleString('en-IN')} · ${pct}%</div>
                </div>
            `;
        }).join('');

        // Build the conic-gradient donut from real budget proportions.
        const donut = document.getElementById('budget-donut-chart');
        if (donut && entries.length) {
            let acc = 0;
            const stops = entries.map(([k, v]) => {
                const start = (acc / sum) * 360;
                acc += v;
                const end = (acc / sum) * 360;
                return `${colors[k] || '#94A3B8'} ${start}deg ${end}deg`;
            }).join(', ');
            donut.style.background = `conic-gradient(${stops})`;
        }
    }

    function renderWeather(forecastData) {
        const container = document.getElementById('weather-container');
        if (!container) return;
        container.innerHTML = '';

        if (!Array.isArray(forecastData) || forecastData.length === 0) {
            container.innerHTML = '<p class="text-muted">No weather forecast available.</p>';
            return;
        }

        const strip = document.createElement('div');
        strip.className = 'd-flex overflow-auto gap-3 pb-2';

        forecastData.slice(0, 7).forEach(day => {
            const card = document.createElement('div');
            card.className = 'glass p-3 rounded-4 flex-shrink-0 text-center border';
            card.style.minWidth = '140px';

            let dateStr = day.date;
            try {
                const d = new Date(day.date);
                dateStr = d.toLocaleDateString('en-US', { weekday: 'short', month: 'short', day: 'numeric' });
            } catch (e) { }

            card.innerHTML = `
                <div class="fw-bold mb-2">${escapeHtml(dateStr)}</div>
                <div class="fs-1 text-accent mb-2"><i class="fas fa-cloud-sun"></i></div>
                <div class="fw-bold">${escapeHtml(day.max_temp ?? '')}</div>
                <div class="text-muted small">${escapeHtml(day.min_temp ?? '')}</div>
                <div class="text-muted small mt-2"><i class="fas fa-tint text-info me-1"></i> ${escapeHtml(day.precipitation || '0 mm')}</div>
                ${day.precipitation_probability ? `<div class="text-muted small"><i class="fas fa-umbrella text-primary me-1"></i> ${escapeHtml(day.precipitation_probability)}</div>` : ''}
            `;
            strip.appendChild(card);
        });

        container.appendChild(strip);
    }

    function renderTransport(options) {
        const container = document.getElementById('transport-options');
        if (!container) return;
        container.innerHTML = '';

        const list = Array.isArray(options) ? options : (options && typeof options === 'object' ? Object.entries(options).map(([type, d]) => ({ type, ...(typeof d === 'object' ? d : { detail: d }) })) : []);
        if (list.length === 0) {
            container.innerHTML = '<p class="text-muted">No transport options available from your origin.</p>';
            return;
        }

        const typeIcons = { flight: 'fa-plane', train: 'fa-train', bus: 'fa-bus', car: 'fa-car', driving: 'fa-car' };

        list.forEach(opt => {
            const type = opt.type || opt.mode || 'route';
            const icon = typeIcons[String(type).toLowerCase()] || 'fa-route';
            const card = document.createElement('div');
            card.className = 'glass p-4 rounded-4 d-flex align-items-center justify-content-between mb-3';
            card.innerHTML = `
                <div class="d-flex align-items-center gap-3">
                    <div class="bg-primary text-white p-3 rounded-circle" style="width:50px;height:50px;display:flex;align-items:center;justify-content:center;">
                        <i class="fas ${icon} fs-4"></i>
                    </div>
                    <div>
                        <h5 class="Bricolage mb-1 text-capitalize">${escapeHtml(type)}</h5>
                        <div class="text-secondary small"><i class="fas fa-clock me-1"></i> ${escapeHtml(opt.duration || opt.detail || 'Unknown')}</div>
                    </div>
                </div>
                <div class="text-end">
                    <div class="fw-bold fs-5 text-accent">${escapeHtml(opt.cost || opt.price || 'Price var.')}</div>
                </div>
            `;
            container.appendChild(card);
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
        const email = emailInput ? emailInput.value.trim() : '';
        const statusEl = document.getElementById('email-success-state');
        if (!email) {
            if (statusEl) { statusEl.classList.remove('d-none', 'text-success'); statusEl.classList.add('text-danger'); statusEl.innerHTML = '<i class="fas fa-exclamation-triangle me-1"></i> Please enter an email address.'; }
            return;
        }
        state.email = email;
        const btn = document.getElementById('btn-send-itinerary-email');
        btn.innerHTML = '<i class="fas fa-spinner fa-spin"></i> Sending...';
        btn.disabled = true;

        try {
            const response = await fetch('/api/email', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ email })
            });
            const data = await response.json();
            if (response.ok && data.status === 'success') {
                btn.innerHTML = 'Sent Successfully';
                if (statusEl) { statusEl.classList.remove('d-none', 'text-danger'); statusEl.classList.add('text-success'); statusEl.innerHTML = '<i class="fas fa-check-circle me-1"></i> Sent successfully!'; }
                showToast(`Itinerary sent to ${email}.`, 'success');
            } else {
                throw new Error(data.message || 'Email delivery failed.');
            }
        } catch (error) {
            btn.innerHTML = 'Send Itinerary';
            btn.disabled = false;
            if (statusEl) { statusEl.classList.remove('d-none', 'text-success'); statusEl.classList.add('text-danger'); statusEl.innerHTML = `<i class="fas fa-exclamation-triangle me-1"></i> ${error.message}`; }
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
        console.error('Email modal not found');
    }
};
