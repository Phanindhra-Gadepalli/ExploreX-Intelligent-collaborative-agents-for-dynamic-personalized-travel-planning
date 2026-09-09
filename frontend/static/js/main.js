/* ==========================================================================
   ExploreX v4.0 "Midnight Aurora" — Frontend Controller
   Preserves the full backend contract (/api/stream SSE, /api/reset,
   /api/nearby) and adds the Focus-Window workspace + fullscreen details.
   ========================================================================== */
document.addEventListener('DOMContentLoaded', function () {
    'use strict';

    // === CORE STATE ===
    const WORLD_DEFAULT_CENTER = [20.0, 0.0];
    const WORLD_DEFAULT_ZOOM = 2;

    let map = null;
    let mapMarkers = [];
    let selectedMarkers = [];
    let routePolylines = [];
    let routeMarkers = [];
    let currentAttractions = [];
    let selectedAttractions = [];
    let _lastOptimalRoute = null;
    let _attractionModal = null;

    let state = {
        step: 'chat',
        userInfo: {},
        attractions: [],
        selectedAttractions: [],
        itinerary: null,
        budget: null,
        ai_recommendation_generated: false,
        user_input_processed: false,
        session_id: null,
        rental_post: null,
        force_continue: false,
        selectedAccommodation: null
    };

    // === DOM CACHE ===
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const chatContainer = document.getElementById('chat-container');
    const loadingSpinner = document.getElementById('loading-spinner');
    const resetBtn = document.getElementById('reset-btn');
    const stepNav = document.getElementById('step-nav');
    const missingFieldsContainer = document.getElementById('missing-fields-container');
    const missingFieldsText = document.getElementById('missing-fields-text');

    // === INIT ===
    initializeCoreUI();
    initializeMap();
    initFocusPanels();

    function initializeCoreUI() {
        updateViewState(state.step);
        // auto-scroll observer for any injected .scroll-container
        const so = new MutationObserver(() => {
            if (document.querySelector('.scroll-container')) { initAutoScroll(); so.disconnect(); }
        });
        so.observe(document.body, { childList: true, subtree: true });
    }

    function initializeMap() {
        try {
            if (!document.getElementById('map')) { console.error('Map container not found.'); return; }
            if (typeof L === 'undefined') { console.error('Leaflet not loaded'); return; }
            map = L.map('map').setView(WORLD_DEFAULT_CENTER, WORLD_DEFAULT_ZOOM);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap contributors'
            }).addTo(map);
        } catch (e) { console.error('Map init error:', e); }
    }

    function inr(amount) {
        if (amount == null || isNaN(amount)) return '₹0';
        return '₹' + Number(amount).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }

    /* ----------------------------------------------------------------------
       FOCUS WINDOW — click a panel name => fullscreen; chat open by default
       ---------------------------------------------------------------------- */
    function initFocusPanels() {
        document.querySelectorAll('.focus-item').forEach(item => {
            const header = item.querySelector('.focus-header');
            if (!header) return;
            header.addEventListener('click', () => {
                if (item.classList.contains('open')) closeFocus();
                else openFocus(item);
            });
        });
        // add aurora orbs to body for extra wow
        ['o1', 'o2', 'o3'].forEach(c => {
            const orb = document.createElement('div');
            orb.className = 'aurora-orb ' + c;
            document.body.appendChild(orb);
        });
    }

    function openFocus(itemOrName) {
        const item = typeof itemOrName === 'string'
            ? document.querySelector(`.focus-item[data-focus="${itemOrName}"]`)
            : itemOrName;
        if (!item) return;
        document.querySelectorAll('.focus-item.open').forEach(i => i.classList.remove('open'));
        item.classList.add('open');
        const focus = item.dataset.focus;
        if (focus === 'map') {
            setTimeout(() => { if (map) { map.invalidateSize(); if (_lastOptimalRoute) drawRoute(_lastOptimalRoute); } }, 350);
        }
    }

    function closeFocus() {
        document.querySelectorAll('.focus-item.open').forEach(i => i.classList.remove('open'));
    }

    function isFocusOpen(name) {
        const el = document.querySelector(`.focus-item[data-focus="${name}"]`);
        return !!(el && el.classList.contains('open'));
    }

    /* ----------------------------------------------------------------------
       AUTO-SCROLL (popular attractions strip, if injected by backend)
       ---------------------------------------------------------------------- */
    function initAutoScroll() {
        const sc = document.querySelector('.scroll-container');
        if (!sc) return;
        sc.innerHTML += sc.innerHTML;
        let speed = 1, paused = false;
        (function loop() {
            if (!paused) {
                sc.scrollTop += speed;
                if (sc.scrollTop >= sc.scrollHeight / 2) sc.scrollTop = 0;
            }
            requestAnimationFrame(loop);
        })();
        sc.addEventListener('mouseenter', () => paused = true);
        sc.addEventListener('mouseleave', () => paused = false);
    }

    /* ----------------------------------------------------------------------
       STEP NAV
       ---------------------------------------------------------------------- */
    function updateStepNav(step) {
        if (!stepNav) return;
        const order = ['chat', 'recommend', 'route'];
        let n = step;
        if (['retrieval', 'information'].includes(step)) n = 'chat';
        if (['strategy', 'communication'].includes(step)) n = 'recommend';
        if (step === 'complete') n = 'route';
        let idx = order.indexOf(n); if (idx === -1) idx = 0;

        stepNav.querySelectorAll('.step-nav-item').forEach((link, i) => {
            const icon = link.querySelector('i');
            link.classList.remove('active', 'completed', 'upcoming');
            if (i < idx) { link.classList.add('completed'); icon.className = 'fas fa-check-circle'; icon.style.color = 'var(--teal-light)'; }
            else if (i === idx) { link.classList.add('active'); icon.className = i === 0 ? 'fas fa-comment-dots' : (i === 1 ? 'fas fa-suitcase' : 'fas fa-route'); icon.style.color = ''; }
            else { link.classList.add('upcoming'); icon.className = 'far fa-circle'; icon.style.color = ''; }
        });
    }

    /* ----------------------------------------------------------------------
       VIEW SWITCHING (masters are moved between slots)
       ---------------------------------------------------------------------- */
    function updateViewState(step) {
        document.querySelectorAll('.view-section').forEach(el => el.classList.remove('active', 'fade-in'));
        const chatMaster = document.getElementById('chat-card-master');
        const mapMaster = document.getElementById('map-card-master');

        if (step === 'chat' || step === 'retrieval' || step === 'information') {
            document.getElementById('view-landing').classList.add('active', 'fade-in');
            if (chatMaster) document.getElementById('chat-column-landing').appendChild(chatMaster);
        }
        else if (step === 'recommend' || step === 'strategy' || step === 'communication') {
            document.getElementById('view-recommendations').classList.add('active', 'fade-in');
            if (chatMaster) document.getElementById('chat-column-recs').appendChild(chatMaster);
            if (mapMaster) document.getElementById('map-container-recs').appendChild(mapMaster);
            // initially chat window is displayed
            setTimeout(() => {
                openFocus('chat');
                if (map) map.invalidateSize();
            }, 300);
        }
        else if (step === 'route' || step === 'complete') {
            document.getElementById('view-plan').classList.add('active', 'fade-in');
            if (mapMaster) document.getElementById('map-container-plan').appendChild(mapMaster);
            setTimeout(() => {
                openFocus('itinerary');
                if (map) {
                    map.invalidateSize();
                    if (_lastOptimalRoute && _lastOptimalRoute.length >= 2) drawRoute(_lastOptimalRoute);
                }
            }, 350);
        }
    }

    /* ----------------------------------------------------------------------
       MISSING FIELDS CHIPS
       ---------------------------------------------------------------------- */
    function showMissingFields(fields) {
        if (!missingFieldsContainer) return;
        
        missingFieldsText.innerHTML = `Please provide: <strong style="color:#fff">${fields.map(f => f.replace(/_/g, ' ')).join(', ')}</strong>`;
        
        // Remove any existing chips container if it was previously added
        const chipsWrap = missingFieldsContainer.querySelector(':scope > div:nth-child(2)');
        if (chipsWrap) {
            chipsWrap.remove();
        }
        
        missingFieldsContainer.classList.remove('d-none');
    }
    
    function hideMissingFields() {
        if (missingFieldsContainer) {
            missingFieldsContainer.classList.add('d-none');
        }
    }

    /* ----------------------------------------------------------------------
       CHAT RENDERING
       ---------------------------------------------------------------------- */
    function scrollToBottom() {
        if (!chatContainer) return;
        requestAnimationFrame(() => chatContainer.scrollTop = chatContainer.scrollHeight);
        setTimeout(() => chatContainer.scrollTop = chatContainer.scrollHeight, 100);
        chatContainer.querySelectorAll('img:not(.scroll-handled)').forEach(img => {
            img.classList.add('scroll-handled');
            img.addEventListener('load', () => chatContainer.scrollTop = chatContainer.scrollHeight);
        });
    }

    function addChatMessage(message, role) {
        try {
            const div = document.createElement('div');
            div.className = `chat-message ${role}`;
            const content = document.createElement('div');
            content.className = 'message-content';
            content.innerHTML = marked.parse(message);
            div.appendChild(content);
            chatContainer.appendChild(div);
            scrollToBottom();
        } catch (e) { console.error('render error:', e); }
    }

    /* ----------------------------------------------------------------------
       FORM SUBMISSION
       ---------------------------------------------------------------------- */
    chatForm.addEventListener('submit', function (e) {
        e.preventDefault();
        const message = userInput.value.trim();
        if (message) {
            addChatMessage(message, 'user');
            userInput.value = '';
            processUserInput(message);
        }
    });

    resetBtn.addEventListener('click', resetConversation);

    /* ----------------------------------------------------------------------
       SSE STREAM — backend conversation pipeline (UNCHANGED CONTRACT)
       ---------------------------------------------------------------------- */
    function processUserInput(message) {
        loadingSpinner.classList.remove('d-none');

        const messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message assistant';
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        messageDiv.appendChild(messageContent);
        chatContainer.appendChild(messageDiv);

        const params = new URLSearchParams({ step: state.step, user_input: message, session_id: state.session_id || '' });
        if (state.step === 'recommend' && state.selectedAttractions.length > 0)
            params.append('selected_attraction_ids', JSON.stringify(state.selectedAttractions.map(a => a.id)));
        if (state.step === 'recommend' && state.selectedAccommodation)
            params.append('selected_accommodation_id', state.selectedAccommodation.id);
        if (state.step === 'recommend' && state.force_continue)
            params.append('force_continue', 'true');
        params.append('ai_recommendation_generated', String(state.ai_recommendation_generated));
        params.append('user_input_processed', String(state.user_input_processed));

        const eventSource = new EventSource(`/api/stream?${params.toString()}`);
        let fullResponse = '';

        eventSource.onmessage = function (event) {
            let data;
            try { data = JSON.parse(event.data); }
            catch (e) { console.error('JSON parse error:', event.data, e); return; }

            if (data.type === 'chunk') {
                fullResponse += data.content;
                messageContent.innerHTML = marked.parse(fullResponse);
                scrollToBottom();
            }
            else if (data.type === 'complete') {
                eventSource.close();
                loadingSpinner.classList.add('d-none');

                // Validation warning flow (unchanged)
                if (data.validation_warning) {
                    const valMsg = document.getElementById('validation-message');
                    if (valMsg) valMsg.textContent = `You have selected ${data.selected_count} attractions. For a balanced trip, we recommend at least ${data.required_count}.`;
                    const modal = new bootstrap.Modal(document.getElementById('validationModal'));
                    modal.show();
                    document.getElementById('force-proceed-btn').onclick = function () {
                        modal.hide();
                        state.force_continue = true;
                        processUserInput('Here are my selected attractions');
                    };
                    return;
                }
                state.force_continue = false;

                const prevStep = state.step;
                state.step = data.next_step || state.step;
                if (data.next_step) {
                    updateStepNav(data.next_step);
                    updateViewState(state.step);
                }

                if (data.session_id) state.session_id = data.session_id;

                if (data.missing_fields && data.missing_fields.length > 0) showMissingFields(data.missing_fields);
                else hideMissingFields();

                if (data.state) {
                    if (data.state.user_info) state.userInfo = data.state.user_info;
                    if (data.state.attractions) state.attractions = data.state.attractions;
                    if (data.state.selected_attractions) state.selectedAttractions = data.state.selected_attractions;
                    if (data.state.itinerary) state.itinerary = data.state.itinerary;
                    if (data.state.budget) state.budget = data.state.budget;
                    if (data.state.weather_summary) state.weatherSummary = data.state.weather_summary;
                    if (data.state.ai_recommendation_generated !== undefined) state.ai_recommendation_generated = Boolean(data.state.ai_recommendation_generated);
                    if (data.state.user_input_processed !== undefined) state.user_input_processed = Boolean(data.state.user_input_processed);
                }

                if (data.attractions) updateAttractions(data.attractions, data.accommodations || []);
                if (data.map_data) updateMap(data.map_data);
                if (data.itinerary) updateItinerary(data.itinerary);
                if (data.budget) updateBudget(data.budget);
                if (data.transit_options) updateTransitOptions(data.transit_options);
                if (state.weatherSummary) updateWeather(state.weatherSummary);
                if (data.response) updateConfirmation(data.response);

                if (state.step === 'complete') {
                    addChatMessage('Your itinerary and budget have been generated! Open the panels on the right for full-screen details.', 'assistant');
                }

                if (data.optimal_route && data.optimal_route.length >= 2) {
                    _lastOptimalRoute = data.optimal_route;
                    drawRoute(data.optimal_route);
                }

                // strategy-step UX: prefill confirmation + pulse input (unchanged logic)
                if (state.step === 'strategy' && data.next_step === 'strategy') {
                    const ui = document.getElementById('user-input');
                    if (ui) {
                        ui.value = 'I am satisfied with your recommendation, let us go to next step';
                        scrollToBottom(); ui.focus();
                        setTimeout(() => {
                            const confEl = document.getElementById('confirmationModal');
                            if (confEl) {
                                const confModal = new bootstrap.Modal(confEl);
                                const applyHighlight = () => {
                                    ui.classList.add('highlight-input-dark');
                                    const btn = ui.closest('form').querySelector('button[type="submit"]');
                                    if (btn) btn.classList.add('highlight-input-dark');
                                    ui.focus();
                                    const remove = () => {
                                        ui.classList.remove('highlight-input-dark');
                                        if (btn) btn.classList.remove('highlight-input-dark');
                                        ui.removeEventListener('input', remove);
                                        if (btn) btn.removeEventListener('click', remove);
                                    };
                                    ui.addEventListener('input', remove);
                                    if (btn) btn.addEventListener('click', remove);
                                    confEl.removeEventListener('hidden.bs.modal', applyHighlight);
                                };
                                confEl.addEventListener('hidden.bs.modal', applyHighlight);
                                confModal.show();
                            }
                        }, 200);
                    }
                }
            }
            else if (data.type === 'error') {
                eventSource.close();
                loadingSpinner.classList.add('d-none');
                messageContent.innerHTML = 'Sorry, there was an error processing your request. Please try again.';
                if (state.step === 'route' || state.step === 'complete') alert('Error: ' + (data.error || 'Please try again.'));
            }
        };

        eventSource.onerror = function () {
            eventSource.close();
            loadingSpinner.classList.add('d-none');
            messageContent.innerHTML = 'Sorry, a network error occurred. Please try again.';
            if (state.step === 'route' || state.step === 'complete') alert('Network error while generating your itinerary. Please try again.');
        };
    }

    /* ----------------------------------------------------------------------
       TRANSIT
       ---------------------------------------------------------------------- */
    function updateTransitOptions(options) {
        if (!options || typeof options !== 'object') return;
        function renderList(items, iconClass) {
            if (!items || items.length === 0) return '<p class="text-center muted small mb-0">No options available for this route.</p>';
            let html = '';
            items.forEach(opt => {
                const price = opt.price_inr || opt.price || 0;
                const operator = opt.operator || opt.provider || 'Unknown';
                const dep = opt.departure_time || opt.departure || '';
                const arr = opt.arrival_time || opt.arrival || '';
                const dur = opt.duration || '';
                const type = opt.type || '';
                html += `
                <div class="list-group-item transit-row px-3 py-2">
                    <div class="d-flex justify-content-between align-items-start">
                        <div>
                            <i class="${iconClass} text-primary me-2"></i><strong>${operator}</strong>
                            ${type ? `<span class="badge bg-light text-light ms-1 small">${type}</span>` : ''}
                            <div class="small muted mt-1">${dep ? `🛫 ${dep}` : ''} ${arr ? `→ 🛬 ${arr}` : ''} ${dur ? `· ⏱ ${dur}` : ''}</div>
                        </div>
                        <div class="text-end">
                            <span class="fw-bold" style="color: var(--green);">${inr(price)}</span>
                            <div class="small muted">per person</div>
                        </div>
                    </div>
                </div>`;
            });
            return html;
        }
        const f = document.getElementById('flights'), t = document.getElementById('trains'), b = document.getElementById('buses');
        if (f) f.innerHTML = renderList(options.flights, 'fas fa-plane');
        if (t) t.innerHTML = renderList(options.trains, 'fas fa-train');
        if (b) b.innerHTML = renderList(options.buses, 'fas fa-bus');
    }

    /* ----------------------------------------------------------------------
       MAP HELPERS
       ---------------------------------------------------------------------- */
    function clearMarkers(arr) { if (arr) { arr.forEach(m => m.remove()); arr.length = 0; } }
    function updateMap() { /* reserved — selection markers handle the real work */ }

    function addMarkerToMap(attraction) {
        if (!map || !attraction || !attraction.location) return;
        const idx = mapMarkers.findIndex(m => m.attractionId === attraction.id);
        if (idx !== -1) { mapMarkers[idx].remove(); mapMarkers.splice(idx, 1); }
        const marker = L.marker([attraction.location.lat, attraction.location.lng], { title: attraction.name }).addTo(map);
        marker.bindPopup(`<strong>${attraction.name}</strong>`);
        marker.attractionId = attraction.id;
        mapMarkers.push(marker);
        updateMapView();
    }

    function removeMarkerFromMap(id) {
        const i = mapMarkers.findIndex(m => m.attractionId === id);
        if (i !== -1) { mapMarkers[i].remove(); mapMarkers.splice(i, 1); }
        const j = selectedMarkers.findIndex(m => m.attractionId === id);
        if (j !== -1) { selectedMarkers[j].remove(); selectedMarkers.splice(j, 1); }
    }

    function updateMapView() {
        if (!map || mapMarkers.length === 0) return;
        map.fitBounds(L.latLngBounds(mapMarkers.map(m => m.getLatLng())), { maxZoom: 14, padding: [24, 24] });
    }

    function drawRoute(route) {
        if (!map || !route || route.length < 2) return;
        routePolylines.forEach(p => p.remove()); routePolylines = [];
        routeMarkers.forEach(m => m.remove()); routeMarkers = [];
        const path = [];
        route.forEach((spot, index) => {
            if (spot.location && typeof spot.location.lat === 'number' && typeof spot.location.lng === 'number') {
                const pos = [spot.location.lat, spot.location.lng];
                path.push(pos);
                const icon = L.divIcon({
                    className: 'route-marker-icon',
                    html: `<div style="background:linear-gradient(135deg,#2DD4BF,#38BDF8); color:#06281f; border-radius:50%; width:26px; height:26px; display:flex; align-items:center; justify-content:center; font-weight:800; font-size:.8rem; border:2px solid #fff; box-shadow:0 0 12px rgba(45,212,191,.6);">${index + 1}</div>`,
                    iconSize: [26, 26], iconAnchor: [13, 13]
                });
                routeMarkers.push(L.marker(pos, { icon, title: spot.name }).addTo(map));
            }
        });
        if (path.length > 1) {
            routePolylines.push(L.polyline(path, { color: '#2DD4BF', weight: 4, opacity: .85, dashArray: '2 6' }).addTo(map));
        }
        if (path.length > 0) map.fitBounds(L.latLngBounds(path), { padding: [34, 34] });
    }

    /* ----------------------------------------------------------------------
       RECOMMENDATIONS — compact rows; name click => FULLSCREEN detail
       ---------------------------------------------------------------------- */
    function updateAttractions(attractions, accommodations) {
        currentAttractions = attractions || [];
        selectedAttractions = state.selectedAttractions || [];
        state.selectedAccommodation = state.selectedAccommodation || null;

        const confirmFooter = document.getElementById('confirm-all-selections-footer');
        const confirmFooter2 = document.getElementById('recs-card-footer');
        const accCard = document.getElementById('accommodations-card');
        const accContainer = document.getElementById('accommodations-container');
        const staysEmpty = document.getElementById('stays-empty');
        const interestArea = document.getElementById('interest-attractions-area');
        const popularArea = document.getElementById('popular-attractions-area');

        interestArea.innerHTML = ''; popularArea.innerHTML = ''; accContainer.innerHTML = '';

        // ---- accommodations ----
        if (accommodations && accommodations.length > 0) {
            if (accCard) accCard.classList.remove('d-none');
            if (staysEmpty) staysEmpty.classList.add('d-none');
            accommodations.forEach(acc => {
                const div = document.createElement('div');
                div.className = 'acc-item';
                const priceLevel = '💰'.repeat(acc.price_level || 1);
                const rating = acc.rating ? `⭐ ${acc.rating} (${acc.user_ratings_total || 0})` : 'No rating';
                const isSel = state.selectedAccommodation && state.selectedAccommodation.id === acc.id;
                div.innerHTML = `
                    <input class="form-check-input acc-radio" type="radio" name="accommodationOption" ${isSel ? 'checked' : ''}>
                    <div class="flex-grow-1">
                        <p class="acc-name">${acc.name || 'Unknown Stay'}</p>
                        <p class="acc-addr">${acc.address || ''}</p>
                        <span class="acc-badge badge" style="background:rgba(139,92,246,.18); color:#C4B5FD;">${priceLevel}</span>
                        <span class="acc-badge badge" style="background:rgba(255,255,255,.08); color:var(--ink-medium);">${rating}</span>
                    </div>`;
                div.querySelector('.acc-radio').addEventListener('change', () => { state.selectedAccommodation = acc; });
                accContainer.appendChild(div);
            });
        } else {
            if (accCard) accCard.classList.add('d-none');
            if (staysEmpty) staysEmpty.classList.remove('d-none');
        }

        // ---- attraction rows ----
        if (!currentAttractions.length) {
            interestArea.innerHTML = '<div class="empty-state"><p>No recommendations available right now.</p></div>';
            popularArea.innerHTML = '<div class="empty-state"><p>No recommendations available right now.</p></div>';
        } else {
            let interestCount = 0, popularCount = 0;
            currentAttractions.forEach(a => {
                const row = createAttractionRow(a);
                if (a.recommendation_type === 'interest_based') { interestArea.appendChild(row); interestCount++; }
                else { popularArea.appendChild(row); popularCount++; }
            });
            if (!interestCount) interestArea.innerHTML = '<div class="empty-state"><p>No specific matches for your interests yet.</p></div>';
            if (!popularCount) popularArea.innerHTML = '<div class="empty-state"><p>No other popular attractions to show.</p></div>';
        }

        if (confirmFooter) confirmFooter.classList.toggle('d-none', !(currentAttractions.length || (accommodations && accommodations.length)));
        if (confirmFooter2) confirmFooter2.classList.toggle('d-none', !(currentAttractions.length || (accommodations && accommodations.length)));

        // minimize the focus window so the recommendations shine
        setTimeout(closeFocus, 400);
    }

    function createAttractionRow(a) {
        const div = document.createElement('div');
        div.className = 'attraction-row';
        div.dataset.attrId = a.id;
        const isSel = selectedAttractions.some(s => s.id === a.id);
        if (isSel) div.classList.add('selected');
        const rating = a.rating ? `⭐ ${a.rating}` : '☆ New';
        const duration = a.estimated_duration ? `⏱ ${a.estimated_duration}h` : '';
        div.innerHTML = `
            <img class="attr-row-thumb" src="${a.image_url || 'https://via.placeholder.com/120?text=✦'}" onerror="this.onerror=null;this.src='https://via.placeholder.com/120?text=✦';" alt="">
            <div class="attr-row-main">
                <div class="attr-row-name">${a.name}</div>
                <div class="attr-row-meta"><span class="cat">${a.category || 'Experience'}</span><span>${rating}</span><span>${duration}</span></div>
            </div>
            <button class="btn-row-select ${isSel ? 'selected' : ''}"><i class="fas ${isSel ? 'fa-check' : 'fa-plus'}"></i> <span>${isSel ? 'Added' : 'Add'}</span></button>
            <i class="fas fa-chevron-right attr-row-chevron"></i>`;

        // name click => FULLSCREEN detail
        div.querySelector('.attr-row-main').addEventListener('click', () => openAttractionDetail(a.id));
        div.querySelector('.attr-row-thumb').addEventListener('click', () => openAttractionDetail(a.id));
        div.querySelector('.attr-row-chevron').addEventListener('click', () => openAttractionDetail(a.id));
        // quick add button
        div.querySelector('.btn-row-select').addEventListener('click', ev => {
            ev.stopPropagation();
            toggleAttractionSelection(a);
        });
        return div;
    }

    function openAttractionDetail(id) {
        const a = currentAttractions.find(x => x.id === id);
        if (!a) return;
        const isSel = selectedAttractions.some(s => s.id === id);
        const priceLevel = '💰'.repeat(a.price_level || 0) || 'Free / Unknown';
        const rating = a.rating ? `⭐ ${a.rating} (${a.user_ratings_total || 0} reviews)` : 'Not yet rated';
        const duration = a.estimated_duration ? `${a.estimated_duration} hrs` : 'Flexible';

        const body = document.getElementById('attraction-modal-body');
        body.innerHTML = `
            <div class="attr-modal-hero">
                <img src="${a.image_url || 'https://via.placeholder.com/1200x500?text=ExploreX'}" onerror="this.onerror=null;this.src='https://via.placeholder.com/1200x500?text=ExploreX';" alt="${a.name}">
                <button class="attr-modal-close" data-bs-dismiss="modal" aria-label="Close"><i class="fas fa-xmark"></i></button>
            </div>
            <div class="attr-modal-body">
                <h3 class="attr-modal-title">${a.name}</h3>
                <div class="attr-modal-chips">
                    <span class="attr-chip teal"><i class="fas fa-tag"></i> ${a.category || 'Experience'}</span>
                    <span class="attr-chip gold">${priceLevel}</span>
                    <span class="attr-chip">${rating}</span>
                    <span class="attr-chip"><i class="far fa-clock"></i> ${duration}</span>
                </div>
                <p class="attr-modal-desc">${a.description || 'No description available yet — ask the assistant for more!'}</p>
                <div class="attr-modal-actions">
                    <button class="btn btn-primary" id="attr-modal-select"><i class="fas ${isSel ? 'fa-check' : 'fa-plus'} me-2"></i>${isSel ? 'In Your Backpack' : 'Add to Backpack'}</button>
                    <button class="btn btn-ghost" id="attr-modal-nearby"><i class="fas fa-utensils me-2"></i>Nearby Eats</button>
                </div>
                <div id="attraction-nearby"></div>
            </div>`;

        body.querySelector('#attr-modal-select').addEventListener('click', function () {
            toggleAttractionSelection(a);
            const nowSel = selectedAttractions.some(s => s.id === id);
            this.innerHTML = `<i class="fas ${nowSel ? 'fa-check' : 'fa-plus'} me-2"></i>${nowSel ? 'In Your Backpack' : 'Add to Backpack'}`;
        });
        body.querySelector('#attr-modal-nearby').addEventListener('click', () => fetchNearbyPlaces(a, 'attraction-nearby'));

        if (!_attractionModal) _attractionModal = new bootstrap.Modal(document.getElementById('attractionModal'));
        _attractionModal.show();
    }

    /* ----------------------------------------------------------------------
       SELECTION STATE (single source of truth — updates row, modal, map, list)
       ---------------------------------------------------------------------- */
    function toggleAttractionSelection(a) {
        if (!a.id) return;
        const idx = selectedAttractions.findIndex(s => s.id === a.id);
        if (idx > -1) {
            selectedAttractions.splice(idx, 1);
            removeMarkerFromMap(a.id);
        } else {
            selectedAttractions.push(a);
            addMarkerToMap(a);
        }
        state.selectedAttractions = selectedAttractions;
        syncSelectionUI(a.id);
        updateSelectedAttractionsList();
    }

    function syncSelectionUI(id) {
        const sel = selectedAttractions.some(s => s.id === id);
        document.querySelectorAll(`.attraction-row[data-attr-id="${id}"]`).forEach(row => {
            row.classList.toggle('selected', sel);
            const btn = row.querySelector('.btn-row-select');
            if (btn) {
                btn.classList.toggle('selected', sel);
                btn.innerHTML = `<i class="fas ${sel ? 'fa-check' : 'fa-plus'}"></i> <span>${sel ? 'Added' : 'Add'}</span>`;
            }
        });
    }

    // confirm buttons (both the backpack panel and the recommendations card)
    function confirmSelections() {
        if (selectedAttractions.length > 0) {
            state.selectedAttractions = selectedAttractions;
            updateSelectedAttractionsList();
            addChatMessage('Here are my selected attractions', 'user');
            processUserInput('Here are my selected attractions');
        } else {
            addChatMessage('Please select at least one attraction from the recommendations.', 'assistant');
        }
    }
    ['confirm-selected-attractions-btn', 'confirm-selected-attractions-btn-2'].forEach(bid => {
        const b = document.getElementById(bid);
        if (b) b.addEventListener('click', confirmSelections);
    });

    function updateSelectedAttractionsList() {
        const list = document.getElementById('selected-attractions');
        if (!list) return;
        list.innerHTML = '';
        const countBadge = document.getElementById('selected-count');
        if (countBadge) countBadge.textContent = selectedAttractions.length;
        if (!selectedAttractions.length) {
            list.innerHTML = '<div class="empty-state py-3"><p class="mb-0">Select places to add them to your trip.</p></div>';
            return;
        }
        selectedAttractions.forEach(a => {
            const item = document.createElement('div');
            item.className = 'selected-item';
            item.innerHTML = `
                <img src="${a.image_url || 'https://via.placeholder.com/100?text=✦'}" onerror="this.onerror=null;this.src='https://via.placeholder.com/100?text=✦';" class="selected-img" alt="">
                <div class="selected-info">
                    <p class="selected-title" title="${a.name}">${a.name}</p>
                    <p class="selected-cat"><i class="fas fa-tag"></i> ${a.category || 'Location'} · ${a.estimated_duration || 2}h</p>
                </div>
                <button class="btn-remove" title="Remove"><i class="fas fa-times"></i></button>`;
            item.querySelector('.btn-remove').addEventListener('click', () => removeAttraction(a.id));
            list.appendChild(item);
        });
    }

    function removeAttraction(id) {
        selectedAttractions = selectedAttractions.filter(a => a.id !== id);
        state.selectedAttractions = selectedAttractions;
        removeMarkerFromMap(id);
        syncSelectionUI(id);
        updateSelectedAttractionsList();
    }

    /* result cache keyed by "lat,lng" to avoid repeat fetches */
    const _nearbyFoodCache = {};

    function fetchNearbyPlaces(attraction, containerId) {
        const c = document.getElementById(containerId);
        if (!c) return;

        if (!attraction || !attraction.location || typeof attraction.location.lat !== 'number') {
            c.innerHTML = '<p class="text-danger mt-3">Location data not available for this attraction.</p>';
            return;
        }

        const cacheKey = `${attraction.location.lat.toFixed(5)},${attraction.location.lng.toFixed(5)}`;

        // Reuse cached result if available
        if (_nearbyFoodCache[cacheKey]) {
            renderNearbyFood(c, _nearbyFoodCache[cacheKey], attraction.name);
            return;
        }

        c.innerHTML = '<p class="muted mt-3"><i class="fas fa-circle-notch fa-spin me-2"></i>Scouting nearby eats…</p>';

        fetch(`/api/nearby/${cacheKey}`)
            .then(r => { if (!r.ok) throw new Error('HTTP ' + r.status); return r.json(); })
            .then(data => {
                _nearbyFoodCache[cacheKey] = data;
                renderNearbyFood(c, data, attraction.name);
            })
            .catch(() => {
                c.innerHTML = '<p class="text-danger mt-3"><i class="fas fa-exclamation-circle me-2"></i>Unable to load nearby food options right now.</p>';
            });
    }

    function renderNearbyFood(container, data, attractionName) {
        const places = data.restaurants || [];
        if (!places.length) {
            container.innerHTML = `<div class="empty-state mt-3"><p class="mb-0">No food spots found within walking distance of <strong>${attractionName}</strong>.</p></div>`;
            return;
        }

        let html = `<div class="nearby-food-list mt-3">
            <p class="nearby-food-heading"><i class="fas fa-utensils me-2" style="color:var(--coral)"></i>Nearby Eats — <span style="opacity:.7">${attractionName}</span></p>`;

        places.forEach(r => {
            const distText = r.distance_m != null
                ? (r.distance_m >= 1000
                    ? `${(r.distance_m / 1000).toFixed(1)} km away`
                    : `${r.distance_m} m away`)
                : '';

            html += `<div class="nearby-food-card">
                <div class="nearby-food-icon"><i class="fas fa-utensils"></i></div>
                <div class="nearby-food-info">
                    <p class="nearby-food-name">${r.name}</p>
                    <div class="nearby-food-meta">
                        ${r.type ? `<span><i class="fas fa-tag me-1"></i>${r.type}</span>` : ''}
                        ${r.cuisine ? `<span><i class="fas fa-bowl-food me-1"></i>${r.cuisine}</span>` : ''}
                        ${distText ? `<span><i class="fas fa-location-dot me-1"></i>${distText}</span>` : ''}
                    </div>
                    ${r.address ? `<p class="nearby-food-address"><i class="fas fa-map-pin me-1"></i>${r.address}</p>` : ''}
                    ${r.opening_hours ? `<p class="nearby-food-hours"><i class="far fa-clock me-1"></i>${r.opening_hours}</p>` : ''}
                </div>
            </div>`;
        });

        html += `</div>`;
        container.innerHTML = html;
    }


    /* ----------------------------------------------------------------------
       ITINERARY / BUDGET / CONFIRMATION (unchanged logic, midnight styling)
       ---------------------------------------------------------------------- */
    function updateWeather(summary) {
        document.querySelectorAll('.weather-container').forEach(container => {
            if (summary) {
                container.innerHTML = `
                    <div class="d-flex align-items-center gap-3">
                        <i class="fas fa-cloud-sun fa-3x" style="color:var(--teal)"></i>
                        <p class="mb-0" style="color:var(--ink-medium); font-size: 0.95rem;">${summary}</p>
                    </div>`;
            } else {
                container.innerHTML = `<div class="empty-state py-2"><p class="mb-0">Weather information not available.</p></div>`;
            }
        });
    }

    function updateItinerary(itinerary) {
        const c = document.getElementById('itinerary-container');
        if (!c) return;
        c.innerHTML = '';
        if (!Array.isArray(itinerary) || !itinerary.length) {
            c.innerHTML = '<p class="text-center muted">No itinerary available yet.</p>'; return;
        }
        itinerary.forEach(day => {
            const dayDiv = document.createElement('div');
            dayDiv.className = 'itinerary-day';
            dayDiv.innerHTML = `<div class="day-header"><i class="fas fa-sun"></i><div>Day ${day.day || ''} <span class="fw-normal ms-2" style="opacity:.8">${day.date || ''}</span></div></div>`;
            const timeline = document.createElement('div');
            timeline.className = 'timeline';
            (day.spots || []).forEach(spot => {
                const isAcc = spot.is_accommodation || (spot.category && spot.category.toLowerCase().includes('accommodation'));
                const priceLevel = '💰'.repeat(spot.price_level || 0);
                const ev = document.createElement('div');
                ev.className = 'timeline-event';
                ev.innerHTML = `
                    <div class="timeline-dot ${isAcc ? 'accommodation' : ''}"></div>
                    <div class="timeline-content">
                        <div class="event-time"><i class="far fa-clock me-1"></i> ${spot.start_time || ''} – ${spot.end_time || ''}</div>
                        <div class="event-title">${spot.name}</div>
                        <div class="event-meta">
                            <span><i class="fas ${isAcc ? 'fa-bed' : 'fa-map-marker-alt'} muted me-1"></i> ${spot.category || 'Location'}</span>
                            ${priceLevel ? `<span>${priceLevel}</span>` : ''}
                        </div>
                    </div>`;
                timeline.appendChild(ev);
            });
            dayDiv.appendChild(timeline);
            c.appendChild(dayDiv);
        });
    }

    function updateBudget(budget) {
        const c = document.getElementById('budget-container');
        if (!c) return;
        if (!budget || budget.total == null) { c.innerHTML = '<p class="muted">No budget estimate available.</p>'; return; }

        const misc = budget.miscellaneous || Math.round((budget.total || 0) * 0.10);
        const grand = budget.total + (budget.miscellaneous ? 0 : misc);
        const days = budget.days || 1, people = budget.people || 1;
        const perPerson = grand > 0 ? Math.round(grand / people) : 0;
        const numPlaces = state.selectedAttractions ? state.selectedAttractions.length
            : (state.itinerary ? state.itinerary.reduce((acc, d) => acc + (d.spots ? d.spots.length : 0), 0) : 0);

        let warn = '';
        if (budget.budget_warning) {
            warn = `<div class="alert ${budget.budget_infeasible ? 'alert-danger' : 'alert-warning'} small p-2 mb-3 d-flex align-items-start"><span class="me-2">⚠️</span><div>${budget.budget_warning}</div></div>`;
        }
        let target = '';
        if (budget.budget_amount) {
            const over = grand > budget.budget_amount;
            target = `
            <div class="d-flex justify-content-between align-items-center mb-2 mt-3 pt-2" style="border-top:1px solid var(--stroke);">
                <div><div class="small muted">Target Budget</div><div class="fw-bold" style="color:#fff">${inr(budget.budget_amount)}</div></div>
                <div class="text-end"><div class="small muted">${over ? 'Over Budget' : 'Remaining'}</div>
                <div class="fw-bold ${over ? 'text-danger' : 'text-success'}">${inr(Math.abs(budget.budget_amount - grand))}</div></div>
            </div>`;
        }

        const cell = (emoji, label, value, sub, extra) => `
            <div class="col-6"><div class="border rounded p-2 h-100 budget-stat-card" ${extra || ''}>
                <div class="muted small mb-1">${emoji} ${label}</div>
                <div class="fw-bold" style="color:#fff">${value}</div>
                <div class="muted" style="font-size:11px">${sub}</div>
            </div></div>`;

        c.innerHTML = `
        ${warn}
        <div class="row g-2 mb-3 text-center">
            <div class="col-4"><div class="p-2 border rounded h-100 budget-stat-card">
                <div class="fs-5 mb-1">🗓️</div><div class="fw-bold" style="color:#fff">${days} Days</div><div class="small muted">Duration</div></div></div>
            <div class="col-4"><div class="p-2 border rounded h-100 budget-stat-card">
                <div class="fs-5 mb-1">📍</div><div class="fw-bold" style="color:#fff">${numPlaces} Places</div><div class="small muted">Selected</div></div></div>
            <div class="col-4"><div class="p-2 border rounded h-100 budget-total-card">
                <div class="fs-5 mb-1">💰</div><div class="fw-bold" style="color:var(--coral)">${inr(grand)}</div><div class="small muted">Est. Total</div></div></div>
        </div>
        ${target}
        <div class="d-flex justify-content-between align-items-center mb-2 px-1">
            <span class="fw-bold muted small text-uppercase" style="letter-spacing:.5px;">Budget Breakdown</span>
            <span class="small muted">${inr(perPerson)} / person</span>
        </div>
        <div class="row g-2">
            ${cell('🏨', 'Accommodation', inr(budget.accommodation || 0), (budget.rooms || 1) + ' room(s)')}
            ${cell('🍽️', 'Food & Dining', inr(budget.food || 0), 'All meals')}
            ${cell('🚌', 'Local Transport', inr(budget.transport || 0), 'Autos, cabs, buses')}
            ${cell('🎟️', 'Entry Tickets', inr(budget.attractions || 0), 'Attraction fees')}
            ${budget.intercity_transport ? cell('✈️', 'Origin ↔ Dest', inr(budget.intercity_transport), 'Round trip est.') : ''}
            ${budget.car_rental ? cell('🚗', 'Car Rental', inr(budget.car_rental), '') : ''}
            ${budget.fuel_cost ? cell('⛽', 'Fuel', inr(budget.fuel_cost), '') : ''}
            ${cell('🎲', 'Miscellaneous', inr(misc), 'Shopping, tips (~10%)')}
        </div>
        <div class="mt-2 muted small text-center">💡 Estimates in INR. Actual prices may vary.</div>`;
    }

    function updateConfirmation(response) {
        const c = document.getElementById('confirmation-container');
        if (!c) return;
        c.innerHTML = response ? `<div class="message-content" style="max-width:100%">${marked.parse(response)}</div>`
            : '<p class="text-center muted">Trip summary will appear here once generated.</p>';
    }

    /* ----------------------------------------------------------------------
       RESET (backend contract preserved)
       ---------------------------------------------------------------------- */
    function resetConversation() {
        fetch('/api/reset', { method: 'POST' })
            .then(r => r.json())
            .then(() => {
                if (chatContainer) chatContainer.innerHTML = '';
                const it = document.getElementById('itinerary-container');
                if (it) it.innerHTML = '<div class="empty-state"><h5>Your travel plan will appear here once generated.</h5></div>';
                const ia = document.getElementById('interest-attractions-area');
                if (ia) ia.innerHTML = '<div class="empty-state"><i class="fas fa-search"></i><p>No interest-based attractions found.</p></div>';
                const pa = document.getElementById('popular-attractions-area');
                if (pa) pa.innerHTML = '<div class="empty-state"><p>No popular attractions found.</p></div>';
                const bc = document.getElementById('budget-container');
                if (bc) bc.innerHTML = '<div class="empty-state py-2"><p class="mb-0">Budget details will appear here once generated.</p></div>';

                clearMarkers(mapMarkers); clearMarkers(selectedMarkers); clearMarkers(routeMarkers);
                routePolylines.forEach(p => p.remove()); routePolylines = [];
                _lastOptimalRoute = null;
                if (map) map.setView(WORLD_DEFAULT_CENTER, WORLD_DEFAULT_ZOOM);

                state = {
                    step: 'chat', userInfo: {}, attractions: [], selectedAttractions: [], itinerary: null,
                    budget: null, ai_recommendation_generated: false, user_input_processed: false,
                    session_id: null, force_continue: false, selectedAccommodation: null
                };
                selectedAttractions = []; currentAttractions = [];

                closeFocus();
                updateStepNav('chat');
                updateViewState('chat');
                updateSelectedAttractionsList();

                addChatMessage(
                    `Welcome back! I'm your AI travel architect. Tell me where in India you'd like to visit, your budget, and what kind of vibe you're looking for!`, 'assistant');
            })
            .catch(e => console.error('Reset error:', e));
    }

    // expose for inline handlers & debugging
    window.updateMap = updateMap;
    window.removeAttraction = removeAttraction;

    /* ----------------------------------------------------------------------
       LANDING PROMPT → REAL CHAT
       ---------------------------------------------------------------------- */
    const initialPromptForm = document.getElementById('initial-prompt-form');
    const initialUserInput = document.getElementById('initial-user-input');
    const promptChips = document.querySelectorAll('.prompt-chip');

    if (initialPromptForm) {
        initialPromptForm.addEventListener('submit', function (e) {
            e.preventDefault();
            const val = initialUserInput.value;
            if (!val.trim()) return;
            document.getElementById('initial-prompt-ui').classList.add('d-none');
            document.getElementById('landing-hero-text').classList.add('d-none');
            const col = document.getElementById('chat-column-landing');
            if (col) col.classList.remove('d-none');
            updateViewState('chat');
            if (userInput && chatForm) {
                userInput.value = val;
                chatForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
        });
    }
    promptChips.forEach(chip => chip.addEventListener('click', function () {
        if (!initialUserInput) return;
        const text = Array.from(this.childNodes).filter(n => n.nodeType === Node.TEXT_NODE)
            .map(n => n.textContent.trim()).join(' ').trim() || this.innerText.trim();
        const cur = initialUserInput.value.trim();
        initialUserInput.value = cur ? cur + ' ' + text : text;
        initialUserInput.focus();
    }));
});