document.addEventListener('DOMContentLoaded', () => {
    // === Core State ===
    const state = {
        sessionId: null,
        attractions: [],
        itinerary: null,
        budget: null,
        weather: [],
        map: null,
        mapMarkers: [],
        routePolylines: [],
        currentTab: 'chat'
    };

    // === Navigation & Tabs ===
    const navTabs = document.querySelectorAll('.nav-tab');
    const viewSections = document.querySelectorAll('.view-section');

    window.startApp = function() {
        document.getElementById('view-login').classList.remove('active');
        document.getElementById('main-nav').style.display = 'flex';
        switchTab('chat');
        initMap();
    };

    navTabs.forEach(tab => {
        tab.addEventListener('click', () => {
            switchTab(tab.dataset.tab);
        });
    });

    function switchTab(tabId) {
        navTabs.forEach(t => t.classList.remove('active'));
        document.querySelector(`.nav-tab[data-tab="${tabId}"]`)?.classList.add('active');
        
        viewSections.forEach(v => {
            v.classList.remove('active');
            if (v.id === `view-${tabId}`) {
                v.classList.add('active');
            }
        });
        state.currentTab = tabId;
        
        if (tabId === 'map' && state.map) {
            setTimeout(() => state.map.invalidateSize(), 100);
        }
    }

    // === Earth Animation ===
    function initEarth(canvasId, containerId) {
        const canvas = document.getElementById(canvasId);
        const container = document.getElementById(containerId);
        if (!canvas || !container || typeof THREE === 'undefined') return null;

        const scene = new THREE.Scene();
        const camera = new THREE.PerspectiveCamera(38, container.clientWidth / container.clientHeight, 0.1, 1000);
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

        scene.add(new THREE.AmbientLight(0xffffff, 0.8));
        const sun = new THREE.DirectionalLight(0xffffff, 1.5);
        sun.position.set(5, 3, 5);
        scene.add(sun);

        camera.position.z = 7.5;
        
        setTimeout(() => canvas.classList.add('visible'), 500);

        function animate() {
            requestAnimationFrame(animate);
            earth.rotation.y += 0.002;
            clouds.rotation.y += 0.0025;
            renderer.render(scene, camera);
        }
        animate();
        
        window.addEventListener('resize', () => {
            if(container.clientWidth && container.clientHeight) {
                camera.aspect = container.clientWidth / container.clientHeight;
                camera.updateProjectionMatrix();
                renderer.setSize(container.clientWidth, container.clientHeight);
            }
        });
        return { scene, camera, renderer };
    }

    // Initialize Earths
    initEarth('landing-earth-canvas', 'landing-earth-container');
    initEarth('login-earth-canvas', 'login-earth-container');

    // === Small Logo Earth Animation ===
    function initSmallEarths() {
        if (typeof THREE === 'undefined') return;
        const containers = document.querySelectorAll('.earth-logo-container');
        
        const textureLoader = new THREE.TextureLoader();
        const earthTexture = textureLoader.load('https://threejs.org/examples/textures/planets/earth_atmos_2048.jpg');
        
        containers.forEach(container => {
            // Style the container
            container.style.display = 'inline-block';
            container.style.width = '1em';
            container.style.height = '1em';
            container.style.verticalAlign = 'text-bottom';
            container.style.position = 'relative';
            
            const scene = new THREE.Scene();
            const camera = new THREE.PerspectiveCamera(40, 1, 0.1, 100);
            const renderer = new THREE.WebGLRenderer({ alpha: true, antialias: true });
            
            // Set size based on font size. Using a fixed pixel size ensures it fits inside 1em.
            // We'll let CSS scale the canvas.
            renderer.setSize(64, 64);
            renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 2));
            renderer.domElement.style.width = '100%';
            renderer.domElement.style.height = '100%';
            container.appendChild(renderer.domElement);
            
            const group = new THREE.Group();
            scene.add(group);
            
            const earth = new THREE.Mesh(
                new THREE.SphereGeometry(2.2, 32, 32),
                new THREE.MeshPhongMaterial({ map: earthTexture, shininess: 15, specular: new THREE.Color(0x333333) })
            );
            group.add(earth);
            
            const atmosphere = new THREE.Mesh(
                new THREE.SphereGeometry(2.4, 32, 32),
                new THREE.MeshBasicMaterial({ color: 0x38bdf8, transparent: true, opacity: 0.15, side: THREE.BackSide })
            );
            group.add(atmosphere);
            
            scene.add(new THREE.AmbientLight(0xffffff, 0.8));
            const sun = new THREE.DirectionalLight(0xffffff, 1.5);
            sun.position.set(5, 3, 5);
            scene.add(sun);
            
            camera.position.z = 7.5;
            
            function animateSmall() {
                requestAnimationFrame(animateSmall);
                earth.rotation.y += 0.005;
                renderer.render(scene, camera);
            }
            animateSmall();
        });
    }
    initSmallEarths();

    // === Map ===
    function initMap() {
        if (!state.map && document.getElementById('map')) {
            state.map = L.map('map').setView([20.0, 0.0], 2);
            L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
                attribution: '&copy; OpenStreetMap'
            }).addTo(state.map);
        }
    }
    
    // Slider Sync
    const interestWeight = document.getElementById('interest-weight');
    const weightVal = document.getElementById('weight-val');
    if (interestWeight && weightVal) {
        interestWeight.addEventListener('input', (e) => {
            weightVal.innerText = e.target.value + '%';
        });
    }

    // === Backend Communication (SSE) ===
    const chatForm = document.getElementById('chat-form');
    const chatInput = document.getElementById('chat-input-field');
    const chatContainer = document.getElementById('chat-container');

    chatForm.addEventListener('submit', (e) => {
        e.preventDefault();
        const text = chatInput.value.trim();
        if (!text) return;
        
        addChatMessage(text, 'user');
        chatInput.value = '';
        
        triggerBackend('chat', text);
    });

    function triggerBackend(step, userInput, extraParams = {}) {
        let url = `/api/stream?step=${step}&user_input=${encodeURIComponent(userInput)}`;
        if (state.sessionId) url += `&session_id=${state.sessionId}`;
        for (const [k, v] of Object.entries(extraParams)) {
            url += `&${k}=${encodeURIComponent(v)}`;
        }

        const msgDiv = addChatMessage('', 'ai');
        msgDiv.innerHTML = '<i class="fas fa-circle-notch fa-spin"></i> Processing...';
        
        const eventSource = new EventSource(url);
        let fullText = '';
        
        eventSource.onmessage = (e) => {
            const data = JSON.parse(e.data);
            
            if (data.type === 'error') {
                msgDiv.innerHTML = `<span class="text-danger"><i class="fas fa-exclamation-circle"></i> Error: ${data.error}</span>`;
                eventSource.close();
            }
            else if (data.type === 'chunk') {
                if (fullText === '') msgDiv.innerHTML = '';
                fullText += data.content;
                msgDiv.innerHTML = marked.parse(fullText);
                chatContainer.scrollTop = chatContainer.scrollHeight;
            }
            else if (data.type === 'complete') {
                eventSource.close();
                if (data.session_id) state.sessionId = data.session_id;
                
                if (fullText.trim() === '') {
                    msgDiv.innerHTML = "Got it! Preparing the next steps.";
                }

                // Handle Step Outputs
                if (data.next_step === 'recommend' || data.recommended_attractions) {
                    if (data.recommended_attractions) {
                        state.attractions = data.recommended_attractions;
                        renderRecommendations(state.attractions);
                        setTimeout(() => switchTab('explore'), 1500);
                    } else {
                        // Auto trigger recommend if backend expects it
                        triggerBackend('recommend', 'continue');
                    }
                }
                
                if (data.itinerary || data.budget) {
                    if (data.itinerary) {
                        state.itinerary = data.itinerary;
                        renderItinerary(data.itinerary);
                        renderMap(data.itinerary);
                    }
                    if (data.budget) {
                        state.budget = data.budget;
                        renderBudget(data.budget);
                    }
                    if (data.weather_summary) {
                        renderWeather(data.weather_summary);
                    }
                    setTimeout(() => switchTab('itinerary'), 1500);
                }

                // Auto-forwarding internal steps
                if (['information', 'retrieval'].includes(data.next_step)) {
                    triggerBackend(data.next_step, 'continue');
                }
                if (data.next_step === 'route' || data.next_step === 'communication') {
                    triggerBackend(data.next_step, 'continue');
                }
            }
        };
        eventSource.onerror = () => {
            eventSource.close();
            msgDiv.innerHTML = `<span class="text-danger">Connection lost.</span>`;
        };
    }

    function addChatMessage(text, sender) {
        const div = document.createElement('div');
        div.className = sender === 'user' ? 'user-msg' : 'ai-msg';
        
        let icon = sender === 'user' ? '<i class="fas fa-user"></i>' : '<i class="fas fa-robot"></i>';
        
        div.innerHTML = `
            ${sender === 'ai' ? `<div class="avatar">${icon}</div>` : ''}
            <div class="msg-content">${text}</div>
            ${sender === 'user' ? `<div class="avatar bg-primary text-white border-0">${icon}</div>` : ''}
        `;
        
        chatContainer.appendChild(div);
        chatContainer.scrollTop = chatContainer.scrollHeight;
        return div.querySelector('.msg-content');
    }

    // === Render Logic ===
    function renderRecommendations(attrs) {
        const container = document.getElementById('recommendations-container');
        if (!container) return;
        container.innerHTML = '';
        
        attrs.forEach(a => {
            const div = document.createElement('div');
            div.className = 'bento-card';
            div.onclick = () => openAttractionModal(a);
            
            const matchScore = a.score || 85;
            
            const nameFormatted = (a.name || '').toLowerCase().replace(/\s+/g, '-');
            div.id = `attraction-card-${nameFormatted}`;
            div.innerHTML = `
                <img src="${a.image_url || 'https://images.unsplash.com/photo-1469854523086-cc02fe5d8800'}" class="bento-img">
                <div class="bento-content">
                    <div class="d-flex justify-content-between align-items-start mb-2">
                        <h5 class="Bricolage mb-0">${a.name}</h5>
                        <div class="match-badge"><i class="fas fa-check-circle"></i> ${matchScore}% Match</div>
                    </div>
                    <p class="text-muted small mb-3"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${a.address || 'Location'}</p>
                    <div class="d-flex justify-content-between text-secondary small">
                        <span><i class="fas fa-star text-warning me-1"></i> ${a.rating || 4.5}</span>
                        <span><i class="fas fa-clock me-1"></i> ${a.duration || '2h'}</span>
                    </div>
                </div>
                <div class="p-3 border-top text-center text-accent fw-bold" style="background: var(--bg-alt); font-size: 0.9rem;">
                    Explore <i class="fas fa-arrow-right ms-1"></i>
                </div>
            `;
            container.appendChild(div);
        });
    }

    function openAttractionModal(a) {
        document.getElementById('modal-title').innerText = a.name;
        document.getElementById('modal-location').innerText = a.address || 'Unknown Location';
        document.getElementById('modal-rating').innerText = a.rating || 4.5;
        document.getElementById('modal-desc').innerText = a.description || 'No description available.';
        document.getElementById('modal-image').src = a.image_url || 'https://images.unsplash.com/photo-1469854523086-cc02fe5d8800';
        
        new bootstrap.Modal(document.getElementById('attractionModal')).show();
    }

    function renderItinerary(itineraryData) {
        const tl = document.getElementById('itinerary-timeline');
        const mapTl = document.getElementById('map-timeline');
        if (!tl) return;
        tl.innerHTML = '';
        if(mapTl) mapTl.innerHTML = '';

        itineraryData.forEach(day => {
            const dayDiv = document.createElement('div');
            dayDiv.id = `itinerary-day-${day.day}`;
            dayDiv.setAttribute('data-testid', `itinerary-day-${day.day}`);
            dayDiv.innerHTML = `<h4 class="Bricolage mt-5 mb-4 text-accent"><i class="fas fa-sun me-2"></i> Day ${day.day} <span class="text-secondary ms-2" style="font-size: 1.1rem; font-weight: normal;">${day.date || ''}</span></h4>`;
            tl.appendChild(dayDiv);
            
            day.spots.forEach(spot => {
                const item = document.createElement('div');
                item.className = 'timeline-item';
                item.innerHTML = `
                    <div class="timeline-dot"></div>
                    <div class="timeline-time">${spot.start_time || '09:00 AM'}</div>
                    <div class="timeline-content">
                        ${spot.category !== 'Food' ? `<img src="https://images.unsplash.com/photo-1520645521318-f0f1ce215c1e?w=200" class="timeline-img">` : ''}
                        <div>
                            <h5 class="Bricolage mb-1">${spot.name}</h5>
                            <p class="text-secondary small mb-2"><i class="fas fa-map-marker-alt text-accent me-1"></i> ${spot.category || 'Attraction'}</p>
                            <div class="d-flex gap-3 text-muted small">
                                <span><i class="fas fa-clock me-1"></i> ${spot.estimated_duration}h</span>
                            </div>
                        </div>
                    </div>
                `;
                tl.appendChild(item);
                
                if(mapTl && spot.category !== 'Food') {
                    const mItem = item.cloneNode(true);
                    mItem.querySelector('.timeline-img')?.remove();
                    mItem.querySelector('.timeline-content').style.padding = '1rem';
                    mapTl.appendChild(mItem);
                }
            });
        });
    }

    function renderMap(itineraryData) {
        if (!state.map) return;
        
        state.mapMarkers.forEach(m => state.map.removeLayer(m));
        state.routePolylines.forEach(p => state.map.removeLayer(p));
        state.mapMarkers = [];
        state.routePolylines = [];

        let allPoints = [];
        let pointsByDay = [];

        itineraryData.forEach(day => {
            let dayPoints = [];
            day.spots.forEach(s => {
                if (s.location && s.location.lat) {
                    const pt = [s.location.lat, s.location.lng];
                    dayPoints.push(pt);
                    allPoints.push(pt);
                    
                    const m = L.circleMarker(pt, {
                        radius: 8,
                        fillColor: "#0284C7",
                        color: "#fff",
                        weight: 2,
                        fillOpacity: 1
                    }).addTo(state.map);
                    
                    m.on('click', () => {
                        document.getElementById('map-timeline').classList.add('d-none');
                        document.getElementById('map-location-details').classList.remove('d-none');
                        document.getElementById('map-detail-title').innerText = s.name;
                        document.getElementById('map-detail-desc').innerText = s.description || s.category || 'No details available for this location.';
                        document.getElementById('map-detail-img').src = s.image_url || 'https://images.unsplash.com/photo-1520645521318-f0f1ce215c1e?w=800';
                    });
                    
                    state.mapMarkers.push(m);
                }
            });
            if (dayPoints.length > 1) {
                const line = L.polyline(dayPoints, { color: '#0EA5E9', weight: 4, dashArray: '5, 10' }).addTo(state.map);
                state.routePolylines.push(line);
            }
        });

        if (allPoints.length > 0) {
            state.map.fitBounds(L.latLngBounds(allPoints), { padding: [50, 50] });
        }
    }

    function renderBudget(budget) {
        document.getElementById('total-budget-display').innerText = `₹${budget.total || 0}`;
        const container = document.getElementById('budget-breakdown');
        if (!container) return;
        
        const colors = {
            'Accommodation': '#0284C7',
            'Food': '#0EA5E9',
            'Transport': '#38BDF8',
            'Attractions': '#059669',
            'Contingency': '#D97706'
        };
        
        container.innerHTML = Object.entries(budget).filter(([k]) => k !== 'total').map(([k, v]) => `
            <div class="legend-item">
                <div><span class="legend-color" style="background: ${colors[k] || '#E2E8F0'}"></span> <span class="fw-bold">${k}</span></div>
                <div class="text-secondary">₹${v}</div>
            </div>
        `).join('');
    }

    function renderWeather(w) {}

    // Gen Route button
    document.getElementById('btn-generate-route')?.addEventListener('click', () => {
        const ids = state.attractions.map(a => a.id);
        triggerBackend('recommend', 'Here are my selected attractions', {
            selected_attraction_ids: JSON.stringify(ids),
            force_continue: true
        });
        setTimeout(() => triggerBackend('strategy', 'Plan my route'), 2000);
    });

    document.getElementById('btn-send-itinerary-email')?.addEventListener('click', async () => {
        const emailInput = document.getElementById('email-deliver-input');
        const email = emailInput ? emailInput.value : '';
        if (!email) {
            alert('Please enter an email address.');
            return;
        }
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
                document.getElementById('email-success-state').classList.remove('d-none');
                document.getElementById('email-success-state').classList.replace('text-danger', 'text-success');
                document.getElementById('email-success-state').innerHTML = '<i class="fas fa-check-circle me-1"></i> Sent successfully!';
            } else {
                throw new Error(data.message || 'Email delivery failed.');
            }
        } catch (error) {
            btn.innerHTML = 'Send Itinerary';
            btn.disabled = false;
            document.getElementById('email-success-state').classList.remove('d-none');
            document.getElementById('email-success-state').classList.replace('text-success', 'text-danger');
            document.getElementById('email-success-state').innerHTML = `<i class="fas fa-exclamation-triangle me-1"></i> ${error.message}`;
        }
    });
});