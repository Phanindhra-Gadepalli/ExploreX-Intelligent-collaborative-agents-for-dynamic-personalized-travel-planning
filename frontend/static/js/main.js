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
    const missingFieldsContainer = document.getElementById('missing-fields');
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
        const links = stepNav.querySelectorAll('.nav-link');
        links.forEach(link => {
            if (link.dataset.step === step) {
                link.classList.add('active');
            } else {
                link.classList.remove('active');
            }
        });
    }
    // Show missing fields list
    function showMissingFields(fields) {
        if (!missingFieldsContainer) {
            console.log('Missing fields container not found, skipping update');
            return;
        }
        missingFieldsContainer.innerHTML = '<strong>Additional information needed: </strong>' +
            fields.map(f => `<span class="badge bg-warning text-dark me-1">${f}</span>`).join('');
        missingFieldsContainer.classList.remove('d-none');
    }
    
    // Hide missing fields alert
    function hideMissingFields() {
        missingFieldsContainer.classList.add('d-none');
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
        
        if (message) {
            // Add user message to chat
            addChatMessage(message, 'user');
            
            // Clear input
            userInput.value = '';
            
            // Send to backend
            processUserInput(message);
        }
    });
    
    // Reset button
    resetBtn.addEventListener('click', function() {
        resetConversation();
    });
    
    // Add a message to the chat container
    function addChatMessage(message, role) {
        const messageDiv = document.createElement('div');
        messageDiv.className = `chat-message ${role}`;
        
        const messageContent = document.createElement('div');
        messageContent.className = 'message-content';
        
        // Use marked to render Markdown content
        messageContent.innerHTML = marked.parse(message);
        
        messageDiv.appendChild(messageContent);
        chatContainer.appendChild(messageDiv);
        
        // Scroll to bottom
        chatContainer.scrollTop = chatContainer.scrollHeight;
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
            <div class="card-body p-2">
                <div class="row g-2">
                    <div class="col-4">
                        <img src="${attraction.image_url || 'https://via.placeholder.com/150?text=No+Image'}" onerror="this.onerror=null; this.src='https://via.placeholder.com/150?text=No+Image';" alt="${attraction.name}" class="img-fluid rounded" style="height: 100px; width: 100%; object-fit: cover;">
                    </div>
                    <div class="col-8">
                        <h6 class="mb-1 fw-bold text-truncate" title="${attraction.name}">${attraction.name}</h6>
                        <div class="d-flex flex-wrap gap-1 mb-1">
                            <span class="badge bg-light text-dark border"><i class="fas fa-tag text-muted"></i> ${attraction.category || 'N/A'}</span>
                            <span class="badge bg-light text-dark border"><i class="fas fa-clock text-muted"></i> ${duration}</span>
                            <span class="badge bg-light text-dark border">${priceLevel}</span>
                        </div>
                        <div class="mb-2 small text-muted text-truncate" style="max-height: 40px; overflow: hidden;" title="${attraction.description || ''}">${attraction.description || ''}</div>
                        <button class="btn btn-sm w-100 ${isSelected ? 'btn-success' : 'btn-outline-primary'} select-attraction-btn">
                            <i class="fas ${isSelected ? 'fa-check-circle' : 'fa-plus-circle'}"></i> ${isSelected ? 'Selected' : 'Select'}
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
            button.classList.replace('btn-success', 'btn-outline-primary');
            button.innerHTML = `<i class="fas fa-plus-circle"></i> Select`;
        } else {
            selectedAttractions.push(attraction);
            addMarkerToMap(attraction);
            button.classList.replace('btn-outline-primary', 'btn-success');
            button.innerHTML = `<i class="fas fa-check-circle"></i> Selected`;
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
            const dayCard = document.createElement('div');
            dayCard.className = 'card mb-3';
            
            let spotsHTML = '';
            if (day.spots && Array.isArray(day.spots)) {
                day.spots.forEach(spot => {
                    let priceLevel = '';
                    for (let i = 0; i < (spot.price_level || 0); i++) {
                        priceLevel += '💰';
                    }
                    
                    spotsHTML += `
                        <div class="card mb-2">
                            <div class="card-body py-2">
                                <h6 class="mb-1">${spot.name}</h6>
                                <p class="mb-0 small">
                                    <span class="badge bg-primary">${spot.start_time || ''} - ${spot.end_time || ''}</span>
                                    <span class="badge bg-secondary ms-1">${spot.category || 'attraction'}</span>
                                    <span class="ms-2">${priceLevel}</span>
                                </p>
                            </div>
                        </div>
                    `;
                });
            }
            
            dayCard.innerHTML = `
                <div class="card-header bg-light">
                    <strong>Day ${day.day || ''}</strong> - ${day.date || ''}
                </div>
                <div class="card-body">
                    ${spotsHTML}
                </div>
            `;
            
            itineraryContainer.appendChild(dayCard);
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

        budgetContainer.innerHTML = `
        ${warningHtml}
        <div class="budget-summary mb-3 p-3 rounded" style="background:linear-gradient(135deg,#1a472a 0%,#2d6a4f 100%);color:#fff;">
            <div class="d-flex justify-content-between align-items-center">
                <div>
                    <div class="fs-5 fw-bold">💰 Est. Total</div>
                    <div class="small opacity-75">${budget.budget_level ? budget.budget_level.charAt(0).toUpperCase()+budget.budget_level.slice(1)+' Base' : ''} · ${budget.rooms || 1} room(s)</div>
                </div>
                <div class="text-end">
                    <div class="fs-3 fw-bold">${inr(grandTotal)}</div>
                    <div class="small opacity-75">${inr(perPerson)} / person · ${inr(dailyPP)} / day</div>
                </div>
            </div>
            ${targetHtml}
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
                chatContainer.innerHTML = '';
                itineraryContainer.innerHTML = '<p class="text-center text-muted">Your travel plan will appear here once generated.</p>';
                recommendationsContainer.innerHTML = '<p class="text-center text-muted">Recommendations will appear here based on your preferences.</p>';
                document.getElementById('budget-container').innerHTML = '<p class="text-center text-muted">Budget details will appear here once generated.</p>';
                
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
    // Handle attraction selection
    function selectAttraction(attractionId) {
        if (!map) return;
        const attraction = currentAttractions.find(a => a.id === attractionId);
        if (!attraction) return;

        if (!selectedAttractions.some(a => a.id === attractionId)) {
            selectedAttractions.push(attraction);
            updateSelectedAttractionsList();

            const marker = L.marker([attraction.location.lat, attraction.location.lng], {
                icon: L.divIcon({
                    className: 'selected-marker',
                    html: '<div class="selected-marker-inner"></div>',
                    iconSize: [20, 20]
                })
            });
            selectedMarkersLayer.addLayer(marker);
        }
    }

    // Update selected attractions list
    function updateSelectedAttractionsList(attractions) {
        const selectedAttractionsList = document.getElementById('selected-attractions');
        if (!selectedAttractionsList) return;
        
        selectedAttractionsList.innerHTML = '';
        
        if (!attractions || attractions.length === 0) {
            selectedAttractionsList.innerHTML = '<p class="text-center text-muted">No attractions selected yet.</p>';
            return;
        }
        
        attractions.forEach(attraction => {
            const card = document.createElement('div');
            card.className = 'card mb-2';
            
            let priceLevel = '';
            for (let i = 0; i < (attraction.price_level || 0); i++) {
                priceLevel += '💰';
            }
            
            let rating = attraction.rating ? `⭐ ${attraction.rating}` : '';
            
            card.innerHTML = `
                <div class="card-body">
                    <h6 class="card-title mb-1">${attraction.name}</h6>
                    <p class="card-text mb-1">
                        <small class="text-muted">${attraction.category || 'attraction'}</small>
                        <small class="ms-2">${priceLevel}</small>
                        <small class="ms-2">${rating}</small>
                    </p>
                    <small class="text-muted">${attraction.estimated_duration || 2} hours</small>
                </div>
            `;
            
            selectedAttractionsList.appendChild(card);
        });
    }

    // Remove attraction from selection
    function removeAttraction(attractionId) {
        if (!map) return;
        const attraction = selectedAttractions.find(a => a.id === attractionId);
        if (!attraction) return;

        selectedAttractions = selectedAttractions.filter(a => a.id !== attractionId);
        updateSelectedAttractionsList();

        selectedMarkersLayer.eachLayer(layer => {
            if (layer.getLatLng().equals([attraction.location.lat, attraction.location.lng])) {
                selectedMarkersLayer.removeLayer(layer);
            }
        });
    }

    // Add new function to draw route on map
    function drawRoute(route) {
        if (!map || !route || route.length < 2) return;

        // Clear any existing route
        if (window.routeLayer) {
            map.removeLayer(window.routeLayer);
        }

        // Create a new layer for the route
        window.routeLayer = L.layerGroup().addTo(map);

        // const dayColors = ['#FF5733', '#33FF57', '#3357FF', '#FF33A1', '#A133FF', '#33FFA1', '#FFC300', '#C70039']; // Keep for marker colors if needed or define marker colors separately
        // const spotsByDay = {}; // No longer needed for polylines

        // // Group spots by day -- No longer needed for polylines
        // route.forEach(spot => {
        //     if (!spot.day) {
        //         console.warn("Spot missing day information:", spot);
        //         return; 
        //     }
        //     if (!spotsByDay[spot.day]) {
        //         spotsByDay[spot.day] = [];
        //     }
        //     spotsByDay[spot.day].push(spot);
        // });

        const allPolylinesGroup = L.featureGroup().addTo(window.routeLayer);

        // Draw a single polyline for the entire route with a default color
        const allPoints = route.map(spot => {
            if (spot.location && typeof spot.location.lat === 'number' && typeof spot.location.lng === 'number') {
                return [spot.location.lat, spot.location.lng];
            }
            return null; // Handle potential missing/invalid locations
        }).filter(p => p !== null); // Filter out null points

        if (allPoints.length > 1) {
            const polyline = L.polyline(allPoints, {
                color: '#7B8DAB', // New Primary Color (Soft Slate Blue)
                weight: 4,
                opacity: 0.75,
                smoothFactor: 1
            }).addTo(window.routeLayer);
            allPolylinesGroup.addLayer(polyline);
        }

        // // Draw polylines for each day -- REMOVED
        // for (const dayKey in spotsByDay) { ... }

        // Add markers for each point with numbers (iterating the original flat route for sequential numbering)
        route.forEach((spot, index) => {
            if (!spot.location || typeof spot.location.lat !== 'number' || typeof spot.location.lng !== 'number') {
                console.warn("Skipping marker for spot with invalid location:", spot);
                return;
            }
            // Determine day-specific class for the marker background
            const dayNumber = spot.day || 1; // Fallback to day 1 if not specified
            const markerBgClass = `day-${dayNumber}-marker-bg`;

            const marker = L.marker([spot.location.lat, spot.location.lng], {
                icon: L.divIcon({
                    className: `route-marker ${markerBgClass}`, // Add day-specific background class
                    html: `<div class="route-marker-number">${index + 1}</div>`,
                    iconSize: [24, 24],
                    iconAnchor: [12, 12] // Center the number icon
                })
            }).addTo(window.routeLayer);
            
            marker.bindPopup(`
                <h3>${spot.name || 'Unknown'}</h3>
                <p>Stop ${index + 1}</p>
            `);
        });
        
        // Fit bounds to show the entire route if any polylines were drawn
        if (Object.keys(allPolylinesGroup.getLayers()).length > 0) {
            map.fitBounds(allPolylinesGroup.getBounds().pad(0.1));
        } else if (route.length > 0) {
            // Fallback if no polylines (e.g., all days have 1 spot) but markers exist
            const singleMarkersGroup = L.featureGroup(route.map(spot => L.marker([spot.location.lat, spot.location.lng])));
            if (Object.keys(singleMarkersGroup.getLayers()).length > 0) {
                 map.fitBounds(singleMarkersGroup.getBounds().pad(0.2));
            }
        }
    }

    // Modify fetchNearbyPlaces function
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
});
