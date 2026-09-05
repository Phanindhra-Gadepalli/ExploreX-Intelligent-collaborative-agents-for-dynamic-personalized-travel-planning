document.addEventListener('DOMContentLoaded', function() {
    // === CORE STATE & VARIABLES ===
    const WORLD_DEFAULT_CENTER = [20.0, 0.0];
    const WORLD_DEFAULT_ZOOM   = 2;
    
    let map = null;
    let mapMarkers = [];
    let selectedMarkers = [];
    let routePolylines = [];
    let routeMarkers = [];
    let currentAttractions = [];
    let selectedAttractions = [];
    let _lastOptimalRoute = null;

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
        rental_post: null
    };

    // Cache DOM elements
    const chatForm = document.getElementById('chat-form');
    const userInput = document.getElementById('user-input');
    const chatContainer = document.getElementById('chat-container');
    const itineraryContainer = document.getElementById('itinerary-container');
    const recommendationsContainer = document.getElementById('recommendations-container');
    const paginatedRecommendationsContainer = document.getElementById('paginated-recommendations-container');
    const attractionContentArea = document.getElementById('attraction-content-area');
    const confirmAllSelectionsFooter = document.getElementById('confirm-all-selections-footer');
    const confirmSelectedAttractionsBtn = document.getElementById('confirm-selected-attractions-btn');
    const loadingSpinner = document.getElementById('loading-spinner');
    const resetBtn = document.getElementById('reset-btn');
    const stepNav = document.getElementById('step-nav');
    const missingFieldsContainer = document.getElementById('missing-fields-container');
    const missingFieldsText = document.getElementById('missing-fields-text');
    const quickResponseChipsContainer = document.getElementById('quick-response-chips');
    const tripSnapshotContainer = document.getElementById('trip-snapshot-container');
    const tripSnapshotContent = document.getElementById('trip-snapshot-content');
    const selectedAttractionsList = document.getElementById('selected-attractions');

    // === INITIALIZATION ENTRY POINT ===
    initializeCoreUI();
    initializeMap();

    function initializeCoreUI() {
        // Initialize view state on load - this immediately moves chat out of hidden storage
        updateViewState(state.step);
        
        // Start MutationObserver for auto-scroll
        const observer = new MutationObserver((mutations) => {
            if (document.querySelector('.scroll-container')) {
                initAutoScroll();
                observer.disconnect();
            }
        });
        observer.observe(document.body, { childList: true, subtree: true });
    }

    function initializeMap() {
        try {
            const mapElement = document.getElementById('map');
            if (!mapElement) {
                console.error('Map container not found! Map functionality will be disabled.');
                return;
            }
            
            // Initialize Leaflet Map
            if (typeof L !== 'undefined') {
                map = L.map('map').setView(WORLD_DEFAULT_CENTER, WORLD_DEFAULT_ZOOM);
                L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                    attribution: '&copy; OpenStreetMap contributors'
                }).addTo(map);
                console.log('Leaflet Map initialized successfully');
            } else {
                console.error('Leaflet API not loaded');
            }
        } catch (error) {
            console.error('Error initializing Leaflet map:', error);
        }
    }

    // INR currency formatter
    function inr(amount) {
        if (amount == null || isNaN(amount)) return '₹0';
        return '₹' + Number(amount).toLocaleString('en-IN', { maximumFractionDigits: 0 });
    }


    // Auto-scroll for popular attractions
    function initAutoScroll() {
        const scrollContainer = document.querySelector('.scroll-container');
        if (scrollContainer) {
            console.log('Scroll container found, initializing auto-scroll');
            // Duplicate content for seamless looping
            scrollContainer.innerHTML += scrollContainer.innerHTML;

            let scrollSpeed = 1; // Pixels per frame
            let animationFrame;
            let isPaused = false;

            function autoScroll() {
                if (!isPaused) {
                    scrollContainer.scrollTop += scrollSpeed;
                    if (scrollContainer.scrollTop >= scrollContainer.scrollHeight / 2) {
                        scrollContainer.scrollTop = 0;
                    }
                }
                animationFrame = requestAnimationFrame(autoScroll);
            }

            // Pause on hover
            scrollContainer.addEventListener('mouseenter', () => {
                isPaused = true;
            });

            // Resume on mouse leave
            scrollContainer.addEventListener('mouseleave', () => {
                isPaused = false;
            });

            // Start scrolling
            autoScroll();
        } else {
            console.log('Scroll container not found');
        }
    }

    // Use MutationObserver to detect .scroll-container dynamically
    const observer = new MutationObserver((mutations) => {
        if (document.querySelector('.scroll-container')) {
            initAutoScroll();
            observer.disconnect(); // Stop observing once found
        }
    });
    observer.observe(document.body, { childList: true, subtree: true });

    // Update step navigation highlighting
    function updateStepNav(step) {
        if (!stepNav) {
            console.log('Step navigation not found, skipping update');
            return;
        }
        
        // Define logical order
        let stepOrder = ['chat', 'recommend', 'route'];
        // Map backend state names to our nav steps
        let normalizedStep = step;
        if (['retrieval', 'information'].includes(step)) normalizedStep = 'chat';
        if (['strategy', 'communication'].includes(step)) normalizedStep = 'recommend';
        if (step === 'complete') normalizedStep = 'route';
        
        let currentIndex = stepOrder.indexOf(normalizedStep);
        if (currentIndex === -1) currentIndex = 0; // fallback
        
        const links = stepNav.querySelectorAll('.step-nav-item');
        links.forEach((link, index) => {
            const icon = link.querySelector('i');
            
            link.classList.remove('active', 'completed', 'upcoming');
            
            if (index < currentIndex) {
                link.classList.add('completed');
                icon.className = 'fas fa-check-circle text-success';
            } else if (index === currentIndex) {
                link.classList.add('active');
                icon.className = index === 0 ? 'fas fa-comment-dots' : (index === 1 ? 'fas fa-map-marked-alt' : 'fas fa-route');
            } else {
                link.classList.add('upcoming');
                icon.className = 'far fa-circle text-muted';
            }
        });
    }
    // Show missing fields list & Quick Response Chips
    function showMissingFields(fields) {
        if (!missingFieldsContainer || !quickResponseChipsContainer) return;
        console.log(`[UI DEBUG] Showing missing fields: ${fields.join(', ')}`);
        
        let chipHtml = '';
        fields.forEach(f => {
            let options = [];
            if (f === 'destination') options = ['Goa', 'Kerala', 'Rajasthan'];
            else if (f === 'budget') options = ['₹20,000', '₹50,000', '₹1,00,000'];
            else if (f === 'duration') options = ['3 days', '5 days', '1 week'];
            else if (f === 'group_type') options = ['Solo', 'Couple', 'Family', 'Friends'];
            else if (f === 'health_status') options = ['Good health', 'Accessible'];
            else if (f === 'start_date') options = ['Not decided', 'Flexible'];
            else options = ['Flexible', 'Surprise me!'];
            
            options.forEach(opt => {
                chipHtml += `<button type="button" class="chat-quick-chip me-2 mb-2" onclick="document.getElementById('user-input').value='${opt}'; document.getElementById('user-input').focus();">${opt}</button>`;
            });
        });
        
        missingFieldsText.innerHTML = `Please provide: <strong>${fields.map(f => f.replace('_', ' ')).join(', ')}</strong>`;
        quickResponseChipsContainer.innerHTML = chipHtml;
        missingFieldsContainer.classList.remove('d-none');
    }
    
    // Hide missing fields alert
    function hideMissingFields() {
        if (missingFieldsContainer) missingFieldsContainer.classList.add('d-none');
    }
    
    function renderTripSnapshot() {
        if (!tripSnapshotContainer || !tripSnapshotContent) return;
        
        if (!state.userInfo || Object.keys(state.userInfo).length === 0) {
            tripSnapshotContainer.classList.add('d-none');
            return;
        }
        
        let html = '';
        const uiMap = {
            'destination': { icon: 'fa-map-marker-alt', label: 'To' },
            'budget': { icon: 'fa-wallet', label: 'Budget' },
            'duration': { icon: 'fa-clock', label: 'Duration' },
            'group_type': { icon: 'fa-user-friends', label: 'Group' }
        };
        
        let hasData = false;
        for (const [key, config] of Object.entries(uiMap)) {
            if (state.userInfo[key]) {
                hasData = true;
                html += `<div class="bg-light px-2 py-1 rounded"><i class="fas ${config.icon} text-muted me-1"></i> <span class="fw-medium">${state.userInfo[key]}</span></div>`;
            }
        }
        
        if (hasData) {
            tripSnapshotContent.innerHTML = html;
            tripSnapshotContainer.classList.remove('d-none');
        } else {
            tripSnapshotContainer.classList.add('d-none');
        }
    }


    function updateViewState(step) {
        // Remove active class from all views
        document.querySelectorAll('.view-section').forEach(el => {
            el.classList.remove('active', 'fade-in');
        });

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
            
            // Adjust map size after DOM move
            setTimeout(() => { if(map) map.invalidateSize(); }, 300);
        }
        else if (step === 'route' || step === 'complete') {
            document.getElementById('view-plan').classList.add('active', 'fade-in');
            if (mapMaster) document.getElementById('map-container-plan').appendChild(mapMaster);
            
            // Adjust map size after DOM move, then re-draw persisted route
            setTimeout(() => {
                if(map) {
                    map.invalidateSize();
                    // Re-draw route polyline if it was generated before the DOM move
                    if (_lastOptimalRoute && _lastOptimalRoute.length >= 2) {
                        drawRoute(_lastOptimalRoute);
                    }
                }
            }, 300);
        }
    }

    // Handle form submission
    chatForm.addEventListener('submit', function(e) {
        e.preventDefault();
        const message = userInput.value.trim();
        
        console.log(`[UI DEBUG] Submitting message. Length: ${message.length}`);
        
        if (message) {
            // Add user message to chat
            addChatMessage(message, 'user');
            
            // Clear input
            userInput.value = '';
            
            // Send to backend
            console.log(`[UI DEBUG] Sending message to backend. Existing chat flow preserved.`);
            processUserInput(message);
        }
    });
    
    // Reset button
    resetBtn.addEventListener('click', function() {
        resetConversation();
    });
    
    // Add a message to the chat container
    function addChatMessage(message, role) {
        try {
            console.log(`[UI DEBUG] Message rendered for ${role}`);
            const messageDiv = document.createElement('div');
            messageDiv.className = `chat-message ${role}`;
            
            const messageContent = document.createElement('div');
            messageContent.className = 'message-content';
            
            // Use marked to render Markdown content
            messageContent.innerHTML = marked.parse(message);
            
            messageDiv.appendChild(messageContent);
            chatContainer.appendChild(messageDiv);
            
            // Scroll to bottom robustly
            setTimeout(() => {
                chatContainer.scrollTop = chatContainer.scrollHeight;
                console.log(`[UI DEBUG] Scrolling to latest message. Container height: ${chatContainer.clientHeight}px, Scroll Height: ${chatContainer.scrollHeight}px`);
                
                const composer = document.querySelector('.chat-composer');
                if (composer) {
                    const rect = composer.getBoundingClientRect();
                    const style = window.getComputedStyle(composer);
                    console.log(`[UI DEBUG] Composer element found: true`);
                    console.log(`[UI DEBUG] Composer computed display: ${style.display}`);
                    console.log(`[UI DEBUG] Composer computed visibility: ${style.visibility}`);
                    console.log(`[UI DEBUG] Composer bounding rect:`, rect);
                }
            }, 50);
        } catch (error) {
            console.error(`[UI DEBUG] Error rendering message:`, error);
        }
    }
    
    // Process user input by sending to backend
    let processUserInputCount = 0;

    function processUserInput(message) {
        processUserInputCount++;
        console.error(`[DIAGNOSTIC] processUserInput invoked: ${processUserInputCount} times. Message: "${message}". Stack:`, new Error().stack);
        // Show loading spinner
        loadingSpinner.classList.remove('d-none');
        
        // Create a new message container for the assistant's response
        const messageDiv = document.createElement('div');
        messageDiv.className = 'chat-message assistant';
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        messageDiv.appendChild(messageContent);
        chatContainer.appendChild(messageDiv);
        
        // Create EventSource for streaming
        const params = new URLSearchParams({
            step: state.step,
            user_input: message,
            session_id: state.session_id || ''
        });
        
        // Add selected attractions if in recommend step
        if (state.step === 'recommend' && state.selectedAttractions.length > 0) {
            params.append('selected_attraction_ids', JSON.stringify(state.selectedAttractions.map(a => a.id)));
        }
        if (state.step === 'recommend' && state.selectedAccommodation) {
            params.append('selected_accommodation_id', state.selectedAccommodation.id);
        }
        if (state.step === 'recommend' && state.force_continue) {
            params.append('force_continue', 'true');
        }
        
        // Add state flags if they exist
        if (state.ai_recommendation_generated !== undefined) {
            params.append('ai_recommendation_generated', state.ai_recommendation_generated.toString());
        }
        if (state.user_input_processed !== undefined) {
            params.append('user_input_processed', state.user_input_processed.toString());
        }
        
        console.error(`[DIAGNOSTIC] Sending EventSource request with params: ${params.toString()}`);
        console.log('[DEBUG] Current state:', state);
        
        const eventSource = new EventSource(`/api/stream?${params.toString()}`);
        console.error(`[DIAGNOSTIC] EventSource created for session: ${state.session_id}`);
        
        let fullResponse = '';
        let chunkCount = 0;
        
        eventSource.onmessage = function(event) {
            chunkCount++;
            if (chunkCount === 1) {
                console.error(`[DIAGNOSTIC] First chunk received for this EventSource.`);
            }
            let data;
            try {
                data = JSON.parse(event.data);
            } catch (e) {
                console.error('[CRITICAL] JSON parse error on event.data:', event.data, e);
                return; // skip this message, do not crash the handler
            }
            console.log('[DEBUG] Received data:', data);
            
            if (data.type === 'chunk') {
                fullResponse += data.content;
                messageContent.innerHTML = marked.parse(fullResponse);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            } else if (data.type === 'complete') {
                console.error(`[DIAGNOSTIC] EventSource received complete message. Closing connection.`);
                eventSource.close();
                loadingSpinner.classList.add('d-none');
                
                console.log('[DEBUG] processUserInput - Received COMPLETE message with data:', JSON.parse(JSON.stringify(data)));

                if (data.attractions) {
                    console.log('[DEBUG] processUserInput - data.attractions received:', JSON.parse(JSON.stringify(data.attractions)));
                }
                if (data.accommodations) {
                    console.log('[DEBUG] processUserInput - data.accommodations received:', JSON.parse(JSON.stringify(data.accommodations)));
                }
                if (data.map_data) {
                    console.log('[DEBUG] processUserInput - data.map_data received:', JSON.parse(JSON.stringify(data.map_data)));
                }
                
                if (data.validation_warning) {
                    // Show validation modal
                    const valMsg = document.getElementById('validation-message');
                    if(valMsg) {
                        valMsg.textContent = `You have selected ${data.selected_count} attractions. For a balanced trip, we recommend at least ${data.required_count}.`;
                    }
                    const modal = new bootstrap.Modal(document.getElementById('validationModal'));
                    modal.show();
                    
                    document.getElementById('force-proceed-btn').onclick = function() {
                        modal.hide();
                        state.force_continue = true;
                        processUserInput('Here are my selected attractions');
                    };
                    return; // Stop processing so we stay on recommend view
                }
                
                state.force_continue = false; // reset
            
                const prevStep = state.step;
                state.step = data.next_step || state.step;
                if (data.next_step) {
                    updateStepNav(data.next_step);
                    updateViewState(state.step);
                    if (data.next_step === 'strategy') {
                        // Do not auto-forward. Wait for user to confirm in chat.
                        console.log('[DEBUG] Paused at strategy step for user confirmation.');
                    } else if (data.next_step === 'recommend' && !data.validation_warning) {
                        // We are at the recommend step. Show attractions and accommodations.
                        if (data.attractions) {
                            updateAttractions(data.attractions, data.accommodations || []);
                        }
                    }
                }
                
                // Store session_id if provided
                if (data.session_id) {
                    state.session_id = data.session_id;
                    console.log('[DEBUG] Updated session_id:', state.session_id);
                }
                // Display or hide missing fields
                if (data.missing_fields && data.missing_fields.length > 0) {
                    showMissingFields(data.missing_fields);
                } else {
                    hideMissingFields();
                }
                // Update state from response
                if (data.state) {
                    console.log('[DEBUG] Updating state with:', data.state);
                    if (data.state.user_info) state.userInfo = data.state.user_info;
                    if (data.state.attractions) state.attractions = data.state.attractions;
                    if (data.state.selected_attractions) state.selectedAttractions = data.state.selected_attractions;
                    if (data.state.itinerary) state.itinerary = data.state.itinerary;
                    if (data.state.budget) state.budget = data.state.budget;
                    if (data.state.ai_recommendation_generated !== undefined) {
                        state.ai_recommendation_generated = Boolean(data.state.ai_recommendation_generated);
                        console.log('[DEBUG] Updated ai_recommendation_generated:', state.ai_recommendation_generated);
                    }
                    if (data.state.user_input_processed !== undefined) {
                        state.user_input_processed = Boolean(data.state.user_input_processed);
                        console.log('[DEBUG] Updated user_input_processed:', state.user_input_processed);
                    }
                }
                
                // Update Trip Snapshot
                renderTripSnapshot();
                
                // Update UI components
                if (data.attractions) updateAttractions(data.attractions, data.accommodations || []);
                if (data.map_data) updateMap(data.map_data);
                if (data.itinerary) updateItinerary(data.itinerary);
                if (data.budget) updateBudget(data.budget);
                if (data.transit_options) updateTransitOptions(data.transit_options);
                if (data.response) updateConfirmation(data.response);
                // if (data.rental_post) updateRentalPost(data.rental_post); // Remove UI update call
                // 关键修改：如果进入 complete 阶段（即 route 阶段返回 next_step: 'complete'），直接渲染 itinerary 和 budget，不再发起新的请求
                if (state.step === 'complete') {
                    // 已经在本次响应中渲染 itinerary 和 budget，无需再发 step=complete 请求
                    // 可以在此处添加提示或高亮，表示行程已生成
                    addChatMessage('Your itinerary and budget have been generated! Check the left panel for details.', 'assistant');
                }

                // If we have route data, draw it on the map and persist for re-draw after view transitions
                if (data.optimal_route && data.optimal_route.length >= 2) {
                    _lastOptimalRoute = data.optimal_route;
                    drawRoute(data.optimal_route);
                }

                console.log('[DEBUG] Final state:', state);

                
                if (state.step === 'strategy' && data.next_step === 'strategy') {
                    const userInput = document.getElementById('user-input');
                    if (userInput) {
                        userInput.value = 'I am satisfied with your recommendation, let us go to next step';
                        userInput.focus();
                    }
                }
            } else if (data.type === 'error') {
                eventSource.close();
                loadingSpinner.classList.add('d-none');
                messageContent.innerHTML = 'Sorry, there was an error processing your request. Please try again.';
                console.error('Error:', data.error);
                if (state.step === 'route' || state.step === 'complete') {
                    alert('An error occurred while generating your itinerary: ' + (data.error || 'Please try again.'));
                }
            }
        };
        
        eventSource.onerror = function(error) {
            console.error('[DIAGNOSTIC] EventSource failed/errored:', error);
            eventSource.close();
            loadingSpinner.classList.add('d-none');
            messageContent.innerHTML = 'Sorry, there was an error processing your request. Please try again.';
            if (state.step === 'route' || state.step === 'complete') {
                alert('A network error occurred while generating your itinerary. Please try again.');
            }
        };
    }
    
    // Update Transit Options display
    // transit_options is an object: { flights: [...], trains: [...], buses: [...] }
    function updateTransitOptions(options) {
        if (!options || typeof options !== 'object') {
            console.warn('[Transit] No valid transit options received:', options);
            return;
        }

        console.log('[DEBUG] Rendering transit options:', options);

        function renderTransitList(items, iconClass, priceKey) {
            if (!items || items.length === 0) {
                return '<p class="text-muted text-center small">No options available for this route.</p>';
            }
            let html = '<div class="list-group list-group-flush">';
            items.forEach(opt => {
                const price = opt[priceKey || 'price_inr'] || opt.price || 0;
                const operator = opt.operator || opt.provider || 'Unknown';
                const dep = opt.departure_time || opt.departure || '';
                const arr = opt.arrival_time || opt.arrival || '';
                const duration = opt.duration || '';
                const type = opt.type || '';
                html += `
                    <div class="list-group-item px-3 py-2">
                        <div class="d-flex justify-content-between align-items-start">
                            <div>
                                <i class="${iconClass} text-primary me-2"></i>
                                <strong>${operator}</strong>
                                ${type ? `<span class="badge bg-light text-dark ms-1 small">${type}</span>` : ''}
                                <div class="small text-muted mt-1">
                                    ${dep ? `🛫 ${dep}` : ''}
                                    ${arr ? ` → 🛬 ${arr}` : ''}
                                    ${duration ? ` · ⏱ ${duration}` : ''}
                                </div>
                            </div>
                            <div class="text-end">
                                <span class="fw-bold text-success">${inr(price)}</span>
                                <div class="small text-muted">per person</div>
                            </div>
                        </div>
                    </div>`;
            });
            html += '</div>';
            return html;
        }

        const flightsEl = document.getElementById('flights');
        const trainsEl  = document.getElementById('trains');
        const busesEl   = document.getElementById('buses');

        if (flightsEl) flightsEl.innerHTML = renderTransitList(options.flights, 'fas fa-plane');
        if (trainsEl)  trainsEl.innerHTML  = renderTransitList(options.trains,  'fas fa-train');
        if (busesEl)   busesEl.innerHTML   = renderTransitList(options.buses,   'fas fa-bus');
    }
    
    // Leaflet Maps Helper Functions
    function clearMarkers(markerArray) {
        if (markerArray) {
            markerArray.forEach(m => m.remove());
            markerArray.length = 0;
        }
    }

    function updateMap(data) {
        if (!map) return;
        // In original code, updateMap populated `markersLayer` (informational popup markers)
        // But addMarkerToMap populated `mapMarkers` (selected attractions).
        // For simplicity, we just ignore `updateMap`'s marker creation if it conflicts, 
        // but let's implement it for safety if the backend sends `map_data`.
        // Actually, looking at the flow, `updateMap` is barely used because `addMarkerToMap` does the real work.
    }

    // Update attractions and accommodations display
    function updateAttractions(attractions, accommodations) {
        currentAttractions = attractions || [];
        selectedAttractions = state.selectedAttractions || [];
        state.selectedAccommodation = state.selectedAccommodation || null;

        const confirmBtn = document.getElementById('confirm-all-selections-footer');
        const accCard = document.getElementById('accommodations-card');
        const accContainer = document.getElementById('accommodations-container');
        const interestArea = document.getElementById('interest-attractions-area');
        const popularArea = document.getElementById('popular-attractions-area');

        // Reset
        interestArea.innerHTML = '';
        popularArea.innerHTML = '';
        accContainer.innerHTML = '';

        if (!currentAttractions || currentAttractions.length === 0) {
            interestArea.innerHTML = '<p class="text-center text-muted">No recommendations available at the moment.</p>';
            popularArea.innerHTML = '<p class="text-center text-muted">No recommendations available at the moment.</p>';
            if (confirmBtn) confirmBtn.classList.add('d-none');
            if (accCard) accCard.classList.add('d-none');
            return;
        }

        // Render Accommodations
        if (accommodations && accommodations.length > 0) {
            if (accCard) accCard.classList.remove('d-none');
            accommodations.forEach(acc => {
                const accDiv = document.createElement('div');
                accDiv.className = 'card mb-2 shadow-sm border-0';
                
                let priceLevel = '💰'.repeat(acc.price_level || 1);
                let rating = acc.rating ? `⭐ ${acc.rating} (${acc.user_ratings_total || 0} reviews)` : 'No rating';
                
                const isSelected = state.selectedAccommodation && state.selectedAccommodation.id === acc.id;
                
                accDiv.innerHTML = `
                    <div class="card-body p-2 d-flex align-items-center">
                        <div class="me-3">
                            <input class="form-check-input fs-4 acc-radio" type="radio" name="accommodationOption" id="acc-${acc.id}" ${isSelected ? 'checked' : ''}>
                        </div>
                        <div class="flex-grow-1">
                            <h6 class="mb-1 fw-bold">${acc.name || 'Unknown Accommodation'}</h6>
                            <p class="mb-0 text-muted small">${acc.address || ''}</p>
                            <span class="badge bg-secondary me-1">${priceLevel}</span>
                            <span class="badge bg-light text-dark border">${rating}</span>
                        </div>
                    </div>
                `;
                
                const radio = accDiv.querySelector('.acc-radio');
                radio.addEventListener('change', () => {
                    state.selectedAccommodation = acc;
                });
                
                accContainer.appendChild(accDiv);
            });
        }

        // Render Attractions
        let interestCount = 0;
        let popularCount = 0;

        currentAttractions.forEach(attraction => {
            const attrHtml = createAttractionCardHtml(attraction);
            if (attraction.recommendation_type === 'interest_based') {
                interestArea.appendChild(attrHtml);
                interestCount++;
            } else {
                popularArea.appendChild(attrHtml);
                popularCount++;
            }
        });

        if (interestCount === 0) interestArea.innerHTML = '<p class="text-center text-muted small">No specific matches found for your interests.</p>';
        if (popularCount === 0) popularArea.innerHTML = '<p class="text-center text-muted small">No other popular attractions to show.</p>';

        if (confirmBtn) confirmBtn.classList.remove('d-none');
    }

    function createAttractionCardHtml(attraction) {
        const div = document.createElement('div');
        div.className = 'card mb-3 shadow-sm border-0';
        
        let priceLevel = '💰'.repeat(attraction.price_level || 0) || 'Free/Unknown';
        let rating = attraction.rating ? `⭐ ${attraction.rating} (${attraction.user_ratings_total || 0})` : 'No rating';
        let duration = attraction.estimated_duration ? `${attraction.estimated_duration} hrs` : 'N/A';
        
        const isSelected = selectedAttractions.some(sa => sa.id === attraction.id);

        div.innerHTML = `
            <div class="attraction-card ${isSelected ? 'selected' : ''}">
                <div class="attraction-img-container">
                    <img src="${attraction.image_url || 'https://via.placeholder.com/400x200?text=No+Image'}" onerror="this.onerror=null; this.src='https://via.placeholder.com/400x200?text=No+Image';" alt="${attraction.name}" class="attraction-img">
                    <div class="attraction-badges">
                        <span class="badge-glass"><i class="fas fa-tag"></i> ${attraction.category || 'N/A'}</span>
                        <span class="badge-glass"><i class="fas fa-clock"></i> ${duration}</span>
                    </div>
                </div>
                <div class="attraction-body">
                    <h5 class="attraction-title" title="${attraction.name}">${attraction.name}</h5>
                    <div class="mb-2 d-flex justify-content-between align-items-center">
                        <span class="text-success fw-bold small">${priceLevel}</span>
                        <span class="text-warning small">${rating}</span>
                    </div>
                    <div class="attraction-desc" title="${attraction.description || ''}">${attraction.description || 'No description available.'}</div>
                    <div class="attraction-actions">
                        <button class="btn btn-select ${isSelected ? 'selected' : ''} select-attraction-btn">
                            <i class="fas ${isSelected ? 'fa-check' : 'fa-plus'}"></i> ${isSelected ? 'Selected' : 'Select'}
                        </button>
                    </div>
                </div>
            </div>
        `;

        const selectBtn = div.querySelector('.select-attraction-btn');
        selectBtn.addEventListener('click', function() {
            toggleAttractionSelection(attraction, this);
        });

        return div;
    }

    function toggleAttractionSelection(attraction, button) {
        if (!attraction.id) {
            console.error("Cannot toggle attraction with no ID", attraction);
            return;
        }
        const index = selectedAttractions.findIndex(sa => sa.id === attraction.id);
        if (index > -1) {
            selectedAttractions.splice(index, 1);
            removeMarkerFromMap(attraction.id);
            button.classList.replace('selected', 'not-selected');
            button.innerHTML = `<i class="fas fa-plus"></i> Select`;
            button.closest('.attraction-card').classList.remove('selected');
        } else {
            selectedAttractions.push(attraction);
            addMarkerToMap(attraction);
            button.classList.replace('not-selected', 'selected');
            button.classList.add('selected'); // ensure it's added if missing
            button.innerHTML = `<i class="fas fa-check"></i> Selected`;
            button.closest('.attraction-card').classList.add('selected');
        }
        state.selectedAttractions = selectedAttractions;
        updateSelectedAttractionsList();
    }
    
    // Event listener for the main confirm button
    const confirmSelectedAttractionsBtnFooter = document.getElementById('confirm-all-selections-footer');
    if (confirmSelectedAttractionsBtnFooter) {
        confirmSelectedAttractionsBtnFooter.addEventListener('click', () => {
            if (selectedAttractions.length > 0) {
                state.selectedAttractions = selectedAttractions; // Ensure state is up-to-date
                updateSelectedAttractionsList(); // Update UI list
                processUserInput('Here are my selected attractions');
            } else {
                addChatMessage('Please select at least one attraction from the recommendations.', 'assistant');
            }
        });
    }


    // Handle attraction selection (called from map popup or UI)
    function selectAttraction(attractionId) {
        if (!map) return;
        const attraction = currentAttractions.find(a => a.id === attractionId);
        if (!attraction) return;

        if (!selectedAttractions.some(a => a.id === attractionId)) {
            selectedAttractions.push(attraction);
            updateSelectedAttractionsList();

            const customIcon = L.divIcon({
                className: 'custom-leaflet-icon',
                html: '<div style="background-color:#198754; width:16px; height:16px; border-radius:50%; border:2px solid white;"></div>',
                iconSize: [16, 16],
                iconAnchor: [8, 8]
            });

            const marker = L.marker([attraction.location.lat, attraction.location.lng], {
                icon: customIcon,
                title: attraction.name
            }).addTo(map);

            marker.attractionId = attraction.id;
            selectedMarkers.push(marker);
        }
    }

    // Add marker to map (from UI select button)
    function addMarkerToMap(attraction) {
        if (!map || !attraction || !attraction.location) return;

        const existingMarkerIndex = mapMarkers.findIndex(m => m.attractionId === attraction.id);
        if (existingMarkerIndex !== -1) {
            mapMarkers[existingMarkerIndex].remove();
            mapMarkers.splice(existingMarkerIndex, 1);
        }

        const marker = L.marker([attraction.location.lat, attraction.location.lng], {
            title: attraction.name
        }).addTo(map);
        
        marker.bindPopup(`<h5>${attraction.name}</h5>`);
        
        marker.attractionId = attraction.id;
        mapMarkers.push(marker);
        updateMapView();
    }

    // Remove marker from map
    function removeMarkerFromMap(attractionId) {
        const markerIndex = mapMarkers.findIndex(m => m.attractionId === attractionId);
        if (markerIndex !== -1) {
            mapMarkers[markerIndex].remove();
            mapMarkers.splice(markerIndex, 1);
        }
        const selMarkerIndex = selectedMarkers.findIndex(m => m.attractionId === attractionId);
        if (selMarkerIndex !== -1) {
            selectedMarkers[selMarkerIndex].remove();
            selectedMarkers.splice(selMarkerIndex, 1);
        }
    }

    function updateMapView() {
        if (!map || mapMarkers.length === 0) return;
        const bounds = L.latLngBounds(mapMarkers.map(m => m.getLatLng()));
        map.fitBounds(bounds, { maxZoom: 14, padding: [20, 20] });
    }

    function drawRoute(route) {
        if (!map || !route || route.length < 2) return;

        // Clear existing
        routePolylines.forEach(p => p.remove());
        routePolylines = [];
        routeMarkers.forEach(m => m.remove());
        routeMarkers = [];

        const path = [];

        route.forEach((spot, index) => {
            if (spot.location && typeof spot.location.lat === 'number' && typeof spot.location.lng === 'number') {
                const pos = [spot.location.lat, spot.location.lng];
                path.push(pos);

                const icon = L.divIcon({
                    className: 'route-marker-icon',
                    html: `<div style="background-color:#0d6efd; color:white; border-radius:50%; width:24px; height:24px; display:flex; align-items:center; justify-content:center; font-weight:bold; border:2px solid white;">${index + 1}</div>`,
                    iconSize: [24, 24],
                    iconAnchor: [12, 12]
                });

                const marker = L.marker(pos, {
                    icon: icon,
                    title: spot.name
                }).addTo(map);
                routeMarkers.push(marker);
            }
        });

        if (path.length > 1) {
            const polyline = L.polyline(path, {
                color: '#7B8DAB',
                weight: 4,
                opacity: 0.8
            }).addTo(map);
            routePolylines.push(polyline);
        }
        
        if (path.length > 0) {
            const bounds = L.latLngBounds(path);
            map.fitBounds(bounds, { padding: [30, 30] });
        }
    }
    // Update itinerary display
    function updateItinerary(itinerary) {
        const itineraryContainer = document.getElementById('itinerary-container');
        if (!itineraryContainer) return;
        
        itineraryContainer.innerHTML = '';
        
        if (!itinerary || !Array.isArray(itinerary) || itinerary.length === 0) {
            itineraryContainer.innerHTML = '<p class="text-center text-muted">No itinerary available yet.</p>';
            return;
        }
        
        itinerary.forEach(day => {
            const dayDiv = document.createElement('div');
            dayDiv.className = 'itinerary-day';
            
            const header = document.createElement('div');
            header.className = 'day-header';
            header.innerHTML = `<i class="fas fa-sun"></i> <div>Day ${day.day || ''} <span class="fw-normal ms-2 opacity-75">${day.date || ''}</span></div>`;
            dayDiv.appendChild(header);

            const timeline = document.createElement('div');
            timeline.className = 'timeline';

            if (day.spots && Array.isArray(day.spots)) {
                day.spots.forEach(spot => {
                    let priceLevel = '💰'.repeat(spot.price_level || 0);
                    const isAcc = spot.is_accommodation || (spot.category && spot.category.toLowerCase().includes('accommodation'));
                    
                    const eventDiv = document.createElement('div');
                    eventDiv.className = 'timeline-event';
                    eventDiv.innerHTML = `
                        <div class="timeline-dot ${isAcc ? 'accommodation' : ''}"></div>
                        <div class="timeline-content">
                            <div class="event-time"><i class="far fa-clock me-1"></i> ${spot.start_time || ''} - ${spot.end_time || ''}</div>
                            <div class="event-title">${spot.name}</div>
                            <div class="event-meta">
                                <span><i class="fas ${isAcc ? 'fa-bed' : 'fa-map-marker-alt'} text-muted me-1"></i> ${spot.category || 'Location'}</span>
                                ${priceLevel ? `<span>${priceLevel}</span>` : ''}
                            </div>
                        </div>
                    `;
                    timeline.appendChild(eventDiv);
                });
            }
            
            dayDiv.appendChild(timeline);
            itineraryContainer.appendChild(dayDiv);
        });
    }
    
    // Update budget display
    function updateBudget(budget) {
        const budgetContainer = document.getElementById('budget-container');
        if (!budgetContainer) return;
        
        if (!budget || budget.total == null) {
            budgetContainer.innerHTML = '<p class="text-muted">No budget estimate available.</p>';
            return;
        }

        // Compute a 10% miscellaneous buffer if not provided
        const miscAmount = budget.miscellaneous || Math.round((budget.total || 0) * 0.10);
        const grandTotal = budget.total + (budget.miscellaneous ? 0 : miscAmount);
        const numDays    = budget.days || 1;
        const numPeople  = budget.people || 1;
        const dailyPP    = grandTotal > 0 ? Math.round(grandTotal / numDays) : 0;
        const perPerson  = grandTotal > 0 ? Math.round(grandTotal / numPeople) : 0;

        let warningHtml = '';
        if (budget.budget_warning) {
            const alertType = budget.budget_infeasible ? 'alert-danger' : 'alert-warning';
            warningHtml = `
                <div class="alert ${alertType} small p-2 mb-3 d-flex align-items-start" role="alert">
                    <span class="me-2">⚠️</span>
                    <div>${budget.budget_warning}</div>
                </div>
            `;
        }

        let targetHtml = '';
        if (budget.budget_amount) {
            const isOverBudget = grandTotal > budget.budget_amount;
            const remainingColor = isOverBudget ? 'text-danger' : 'text-success';
            const remainingLabel = isOverBudget ? 'Over Budget' : 'Remaining';
            targetHtml = `
                <div class="d-flex justify-content-between align-items-center mb-2 mt-3 pt-2 border-top border-light border-opacity-25">
                    <div>
                        <div class="small opacity-75">Target Budget</div>
                        <div class="fw-bold">${inr(budget.budget_amount)}</div>
                    </div>
                    <div class="text-end">
                        <div class="small opacity-75">${remainingLabel}</div>
                        <div class="fw-bold ${remainingColor}">${inr(Math.abs(budget.budget_amount - grandTotal))}</div>
                    </div>
                </div>
            `;
        }

        const numPlaces = state.selectedAttractions ? state.selectedAttractions.length : (state.itinerary ? state.itinerary.reduce((acc, day) => acc + (day.spots ? day.spots.length : 0), 0) : 0);
        
        budgetContainer.innerHTML = `
        ${warningHtml}
        
        <!-- Top Stats Row -->
        <div class="row g-2 mb-3 text-center">
            <div class="col-4">
                <div class="p-2 border rounded h-100" style="background: var(--clr-surface-off);">
                    <div class="fs-5 mb-1">🗓️</div>
                    <div class="fw-bold" style="color: var(--clr-ink);">${numDays} Days</div>
                    <div class="small text-muted">Duration</div>
                </div>
            </div>
            <div class="col-4">
                <div class="p-2 border rounded h-100" style="background: var(--clr-surface-off);">
                    <div class="fs-5 mb-1">📍</div>
                    <div class="fw-bold" style="color: var(--clr-ink);">${numPlaces} Places</div>
                    <div class="small text-muted">Selected</div>
                </div>
            </div>
            <div class="col-4">
                <div class="p-2 border h-100 rounded" style="background-color: rgba(232, 82, 42, 0.08); border-color: var(--clr-coral) !important;">
                    <div class="fs-5 mb-1">💰</div>
                    <div class="fw-bold" style="color: var(--clr-coral);">${inr(grandTotal)}</div>
                    <div class="small text-muted">Est. Total</div>
                </div>
            </div>
        </div>
        
        ${targetHtml}
        
        <div class="d-flex justify-content-between align-items-center mb-2 px-1">
            <span class="fw-bold text-muted small text-uppercase" style="letter-spacing: 0.5px;">Budget Breakdown</span>
            <span class="small text-muted">${inr(perPerson)} / person</span>
        </div>
        <div class="row g-2">
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🏨 Accommodation</div>
                    <div class="fw-bold">${inr(budget.accommodation || 0)}</div>
                    <div class="text-muted" style="font-size:11px">${budget.rooms || 1} room(s)</div>
                </div>
            </div>
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🍽️ Food & Dining</div>
                    <div class="fw-bold">${inr(budget.food || 0)}</div>
                    <div class="text-muted" style="font-size:11px">All meals</div>
                </div>
            </div>
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🚌 Local Transport</div>
                    <div class="fw-bold">${inr(budget.transport || 0)}</div>
                    <div class="text-muted" style="font-size:11px">Autos, cabs, buses</div>
                </div>
            </div>
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🎟️ Entry Tickets</div>
                    <div class="fw-bold">${inr(budget.attractions || 0)}</div>
                    <div class="text-muted" style="font-size:11px">Attraction fees</div>
                </div>
            </div>
            ${budget.intercity_transport ? `
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">✈️ Origin ↔ Dest</div>
                    <div class="fw-bold">${inr(budget.intercity_transport)}</div>
                    <div class="text-muted" style="font-size:11px">Round trip est.</div>
                </div>
            </div>` : ''}
            ${budget.car_rental ? `
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🚗 Car Rental</div>
                    <div class="fw-bold">${inr(budget.car_rental)}</div>
                </div>
            </div>` : ''}
            ${budget.fuel_cost ? `
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">⛽ Fuel</div>
                    <div class="fw-bold">${inr(budget.fuel_cost)}</div>
                </div>
            </div>` : ''}
            <div class="col-6">
                <div class="border rounded p-2 h-100">
                    <div class="text-muted small mb-1">🎲 Miscellaneous</div>
                    <div class="fw-bold">${inr(miscAmount)}</div>
                    <div class="text-muted" style="font-size:11px">Shopping, tips (~10%)</div>
                </div>
            </div>
        </div>
        <div class="mt-2 text-muted small text-center">
            💡 Estimates are in INR and may vary. Prices are approximate.
        </div>
        `;
    }

    
    // Update confirmation display
    function updateConfirmation(response) {
        const confirmationDiv = document.getElementById('confirmation-container');
        if (!confirmationDiv) return;
        confirmationDiv.innerHTML = '';
        if (!response) {
            confirmationDiv.innerHTML = '<p class="text-center text-muted">Trip confirmation and details will appear here once generated.</p>';
            return;
        }
        // 支持 markdown 格式
        confirmationDiv.innerHTML = `<div class="message-content">${marked.parse(response)}</div>`;
    }
    
    // Reset conversation
    function resetConversation() {
        fetch('/api/reset', { method: 'POST' })
            .then(response => response.json())
            .then(() => {
                // Clear UI
                if (chatContainer) chatContainer.innerHTML = '';
                if (itineraryContainer) itineraryContainer.innerHTML = '<div class="empty-state"><h5>Your travel plan will appear here once generated.</h5></div>';
                
                const intArea = document.getElementById('interest-attractions-area');
                if (intArea) intArea.innerHTML = '<div class="col-12 empty-state"><i class="fas fa-search"></i><p>No interest-based attractions found.</p></div>';
                
                const popArea = document.getElementById('popular-attractions-area');
                if (popArea) popArea.innerHTML = '<div class="col-12 empty-state"><p>No popular attractions found.</p></div>';
                
                const budgetCont = document.getElementById('budget-container');
                if (budgetCont) budgetCont.innerHTML = '<div class="empty-state py-3"><p class="mb-0">Budget details will appear here once generated.</p></div>';
                
                // Clear map markers
                if (typeof clearMarkers !== 'undefined') {
                    clearMarkers(mapMarkers);
                    clearMarkers(selectedMarkers);
                    clearMarkers(routeMarkers);
                    if (routePolylines) { routePolylines.forEach(p => p.setMap(null)); routePolylines = []; }
                }
                
                // Reset map view to world default
                if (map) {
                    map.setCenter({ lat: WORLD_DEFAULT_CENTER[0], lng: WORLD_DEFAULT_CENTER[1] });
                    map.setZoom(WORLD_DEFAULT_ZOOM);
                }
                
                // Reset state
                state = {
                    step: 'chat',
                    userInfo: {},
                    attractions: [],
                    selectedAttractions: [],
                    itinerary: null,
                    budget: null,
                    ai_recommendation_generated: false,
                    user_input_processed: false,
                    session_id: null
                };
                
                // Add initial welcome message
                addChatMessage(
`Welcome to your Travel AI Assistant! Tell me your name, and I'll help you plan your perfect trip. Let's start by gathering some information:
<ul>
  <li>Which city would you like to visit?</li>
  <li>How many days will you stay?</li>
  <li>What's your budget (low, medium, high)?</li>
  <li>How many people are traveling?</li>
  <li>Are you traveling with children, pets, or have any special requirements?</li>
  <li>What type of activities do you enjoy (e.g., adventure, relaxation, culture)?</li>
  <li>What's your health condition?</li>
</ul>
`, 'assistant');
            })
            .catch(error => {
                console.error('Error resetting conversation:', error);
            });
    }
    // NOTE: selectAttraction is defined above (~line 683). Duplicate removed.
    function _removedDuplicateSelectAttraction_doNotCall(attractionId) {}

    // Update selected attractions list
    // NOTE: always reads from module-level selectedAttractions — no parameter needed
    function updateSelectedAttractionsList() {
        const selectedAttractionsList = document.getElementById('selected-attractions');
        if (!selectedAttractionsList) return;
        
        selectedAttractionsList.innerHTML = '';
        
        if (!selectedAttractions || selectedAttractions.length === 0) {
            selectedAttractionsList.innerHTML = '<p class="text-center text-muted">No attractions selected yet.</p>';
            return;
        }
        
        // Update count in header
        const countBadge = document.getElementById('selected-count');
        if (countBadge) countBadge.textContent = `${selectedAttractions.length} items`;

        selectedAttractions.forEach(attraction => {
            const item = document.createElement('div');
            item.className = 'selected-item';
            
            item.innerHTML = `
                <img src="${attraction.image_url || 'https://via.placeholder.com/100?text=NA'}" onerror="this.onerror=null; this.src='https://via.placeholder.com/100?text=NA';" class="selected-img" alt="${attraction.name}">
                <div class="selected-info">
                    <p class="selected-title" title="${attraction.name}">${attraction.name}</p>
                    <p class="selected-cat"><i class="fas fa-tag"></i> ${attraction.category || 'Location'} · ${attraction.estimated_duration || 2}h</p>
                </div>
                <button class="btn-remove" onclick="removeAttraction('${attraction.id}')" title="Remove">
                    <i class="fas fa-times"></i>
                </button>
            `;
            
            selectedAttractionsList.appendChild(item);
        });
    }

    // Remove attraction from selection
    function removeAttraction(attractionId) {
        const attraction = selectedAttractions.find(a => a.id === attractionId);
        if (!attraction) return;

        selectedAttractions = selectedAttractions.filter(a => a.id !== attractionId);
        state.selectedAttractions = selectedAttractions;
        removeMarkerFromMap(attractionId);
        updateSelectedAttractionsList();
    }


    // fetchNearbyPlaces function

    function fetchNearbyPlaces(attraction, containerId) {
        const nearbyContainer = document.getElementById(containerId);
        if (!nearbyContainer) {
            console.error('[ERROR] Nearby container not found for ID:', containerId);
            return;
        }
        nearbyContainer.innerHTML = '<p class="text-muted">Loading nearby information...</p>'; // Ensure this is set before fetch

        console.log('[DEBUG] fetchNearbyPlaces - Attraction:', JSON.parse(JSON.stringify(attraction)), 'Container ID:', containerId);
        if (!attraction || !attraction.location || typeof attraction.location.lat !== 'number' || typeof attraction.location.lng !== 'number') {
           console.error('[ERROR] Invalid attraction object or location for nearby search. Attraction object:', attraction);
           if (attraction) { // Log location details if attraction object itself exists
            console.error('[ERROR] Attraction location object:', attraction.location);
            if (attraction.location) {
                console.error(`[ERROR] Attraction location.lat: ${attraction.location.lat} (type: ${typeof attraction.location.lat})`);
                console.error(`[ERROR] Attraction location.lng: ${attraction.location.lng} (type: ${typeof attraction.location.lng})`);
            } else {
                console.error('[ERROR] attraction.location itself is undefined or null.');
            }
           } else {
            console.error('[ERROR] attraction object itself is undefined or null.');
           }

           if(nearbyContainer) nearbyContainer.innerHTML = '<p class="text-danger">Error: Invalid attraction data for nearby search.</p>';
           return;
        }

        // Use latitude and longitude instead of name
        const coordinates = `${attraction.location.lat},${attraction.location.lng}`;
        console.log(`[DEBUG] Fetching nearby for ${attraction.name} with coordinates: ${coordinates}`);

        fetch(`/api/nearby/${coordinates}`)
            .then(response => {
                if (!response.ok) {
                    throw new Error(`HTTP error! status: ${response.status}`);
                }
                return response.json();
            })
            .then(data => {
                console.log(`[DEBUG] Nearby data received for ${attraction.name}:`, JSON.parse(JSON.stringify(data)));
                // Display nearby information in the specific container
                const nearbyMessage = formatNearbyPlacesMessage(data, attraction.name);
                nearbyContainer.innerHTML = marked.parse(nearbyMessage); // Use marked for consistency if needed
            })
            .catch(error => {
                console.error(`[ERROR] Error fetching nearby places for ${attraction.name}:`, error);
                nearbyContainer.innerHTML = '<p class="text-danger">Could not load nearby information. Check console for details.</p>';
            });
    }

    // Format nearby information message
    function formatNearbyPlacesMessage(data, attractionName) {
        let message = `<h6>Recommendations near ${attractionName}</h6>`;

        // Nearby Restaurants
        if (data.restaurants && data.restaurants.length > 0) {
            message += '<strong>🍽️ Nearby Restaurants:</strong><ul>';
            data.restaurants.forEach(restaurant => {
                message += `<li>`;
                if (restaurant.photos && restaurant.photos.length > 0) {
                    message += `<img src="${restaurant.photos[0].url}" onerror="this.onerror=null; this.src='https://via.placeholder.com/150?text=No+Image';" style="max-width:100px; border-radius:4px; margin-right: 5px;" alt="${restaurant.name}">`;
                }
                message += `<strong>${restaurant.name}</strong> (${restaurant.type || 'Restaurant'}) - Rating: ${restaurant.rating || 'N/A'}⭐, Price: ${'💰'.repeat(restaurant.price_level || 0) || 'N/A'} <br><small>${restaurant.address || ''}</small>`;
                message += `</li>`;
            });
            message += '</ul>';
        } else {
            message += '<p>No nearby restaurants found.</p>';
        }
        
        // You can add other nearby types here (e.g., cafes, shops) if the API provides them

        return message;
    }

    // Make functions available globally
    window.updateMap = updateMap;
    window.selectAttraction = selectAttraction;
    window.removeAttraction = removeAttraction;

    // === INITIAL PROMPT UI LOGIC ===
    const initialPromptForm = document.getElementById('initial-prompt-form');
    const initialUserInput = document.getElementById('initial-user-input');
    const promptChips = document.querySelectorAll('.prompt-chip');

    if (initialPromptForm) {
        initialPromptForm.addEventListener('submit', function(e) {
            e.preventDefault();
            const val = initialUserInput.value;
            if (val.trim() === '') return;
            
            // Hide initial prompt UI
            document.getElementById('initial-prompt-ui').classList.add('d-none');
            document.getElementById('landing-hero-text').classList.add('d-none');
            
            // Show real chat column
            const chatColumn = document.getElementById('chat-column-landing');
            if(chatColumn) chatColumn.classList.remove('d-none');
            
            // Move chat-master to the screen immediately before backend responds
            updateViewState('chat');
            
            // Transfer value and submit real chat
            if (userInput && chatForm) {
                userInput.value = val;
                // Dispatch a submit event on the real chat form
                chatForm.dispatchEvent(new Event('submit', { bubbles: true, cancelable: true }));
            }
        });
    }

    if (promptChips) {
        promptChips.forEach(chip => {
            chip.addEventListener('click', function() {
                if (initialUserInput) {
                    // Strip icon elements, get only text content
                    const chipText = Array.from(this.childNodes)
                        .filter(n => n.nodeType === Node.TEXT_NODE)
                        .map(n => n.textContent.trim())
                        .join(' ')
                        .trim() || this.innerText.trim();
                    const currentVal = initialUserInput.value.trim();
                    initialUserInput.value = currentVal ? currentVal + ' ' + chipText : chipText;
                    initialUserInput.focus();
                }
            });
        });
    }
});
