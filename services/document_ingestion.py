import os
os.environ["ANONYMIZED_TELEMETRY"] = "False"
import json
import sys
from langchain.vectorstores import Chroma
from langchain.embeddings import HuggingFaceEmbeddings
from langchain.schema import Document

# ─────────────────────────────────────────────────────────────────────────────
# COMPREHENSIVE INDIA KNOWLEDGE BASE
# Rich, factual documents covering all major Indian states, cities, tourist
# attractions, cultural sites, food, travel tips, and seasonal information.
# ─────────────────────────────────────────────────────────────────────────────

INDIA_KNOWLEDGE_BASE = [

    # ── DELHI ──────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Delhi (New Delhi) is India's capital and a vibrant mix of ancient history and modern life. "
            "Top attractions include the Red Fort (a UNESCO World Heritage Site, Mughal architecture, open 9:30am–4:30pm), "
            "Qutub Minar (tallest brick minaret in the world, UNESCO heritage), India Gate (war memorial, best at sunset), "
            "Humayun's Tomb (precursor to the Taj Mahal, stunning gardens), Lotus Temple (Bahá'í house of worship, wheelchair accessible), "
            "Akshardham Temple (stunning architecture and light show), and the bustling Chandni Chowk market. "
            "Best time to visit: October to March (pleasant 10–25°C). Avoid November–January due to severe air pollution (AQI can exceed 500). "
            "If air quality is poor, prefer indoor attractions: National Museum (3–4 hours, vast collection), "
            "National Gallery of Modern Art, Crafts Museum. "
            "Delhi Metro is the most convenient transport. Auto-rickshaws and cabs (Ola/Uber) are widely available. "
            "Street food highlights: Paranthe Wali Gali, Karim's for Mughlai, Connaught Place cafes. "
            "Budget options: hostels in Paharganj from ₹500/night. Luxury: The Imperial, Taj Mahal Hotel, The Leela."
        ),
        metadata={"source": "delhi_comprehensive_guide.txt", "category": "city_guide", "region": "north_india", "destination": "Delhi"}
    ),

    # ── AGRA ───────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Agra in Uttar Pradesh is home to the iconic Taj Mahal — one of the Seven Wonders of the World. "
            "The Taj Mahal is a white marble mausoleum built by Emperor Shah Jahan for his wife Mumtaz Mahal. "
            "Best visited at sunrise for golden light and fewer crowds. Entry fee: ₹50 (Indian), ₹1100 (foreign). "
            "Closed on Fridays. Sunrise entry 30 mins before dawn. "
            "Agra Fort (Red Fort of Agra) is another UNESCO heritage site — a massive red sandstone fortress. "
            "Fatehpur Sikri (40 km away) is a ghost Mughal capital with magnificent architecture. "
            "Mehtab Bagh across the river offers the best sunset view of the Taj Mahal. "
            "Best time to visit: October to March. The Taj Mahal is open on full moon nights (book in advance). "
            "Agra is best as a day trip from Delhi (200 km, 2.5 hrs by Gatimaan Express train). "
            "Local specialties: Petha (pumpkin sweet), Dalmoth (spicy snack), Mughal cuisine."
        ),
        metadata={"source": "agra_guide.txt", "category": "heritage", "region": "north_india", "destination": "Agra"}
    ),

    # ── VARANASI ───────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Varanasi (Banaras/Kashi) in Uttar Pradesh is one of the world's oldest living cities and the holiest city in Hinduism. "
            "The ghats along the River Ganges are the heart of the city — there are 88 ghats. "
            "Dashashwamedh Ghat is the most famous, hosting the spectacular Ganga Aarti ceremony every evening at sunset (very crowded, arrive early). "
            "Manikarnika Ghat is the sacred cremation ghat — respectful observation is permitted. "
            "Key temples: Kashi Vishwanath Temple (one of 12 Jyotirlingas), Sankat Mochan Hanuman Temple, Durga Temple. "
            "A dawn boat ride on the Ganges is an unforgettable experience. "
            "Sarnath (10 km away) is where Buddha gave his first sermon — visit the Dhamek Stupa and Sarnath Museum. "
            "Best time to visit: October to March. Dev Deepawali in November (Kartik Purnima) is extraordinary — ghats lit with 1 million lamps. "
            "Food specialties: Banarasi paan, Kachori-sabzi, Thandai, Malaiyo (winter seasonal dessert). "
            "Walking through the narrow bylanes of the old city is essential. Many temples restrict non-Hindus."
        ),
        metadata={"source": "varanasi_guide.txt", "category": "pilgrimage_spiritual", "region": "north_india", "destination": "Varanasi"}
    ),

    # ── JAIPUR ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Jaipur, the Pink City, is the capital of Rajasthan and a UNESCO World Heritage City. "
            "Major attractions: Amer Fort (Amber Fort) — a stunning hilltop fort with mirror work and grand courtyards. "
            "Elephant rides available but controversial; jeep rides are ethical alternative. Amer Fort has uneven terrain and steep steps — limited accessibility for mobility-impaired visitors. "
            "City Palace — still partially used by the royal family; museum with royal artefacts. "
            "Hawa Mahal (Palace of Winds) — iconic 953-window facade, best photographed from the street. "
            "Jantar Mantar — UNESCO heritage astronomical observatory, fascinating for science lovers. "
            "Nahargarh Fort — best sunset view over Jaipur. "
            "Johari Bazaar for gemstones and jewellery; Bapu Bazaar for textiles and handicrafts. "
            "Best time to visit: October to March. "
            "Local cuisine: Dal Baati Churma (must-try), Laal Maas (spicy mutton), Ghewar (dessert). "
            "Accommodation: budget guesthouses in old city, mid-range: Samode Haveli, luxury: Rambagh Palace."
        ),
        metadata={"source": "jaipur_comprehensive_guide.txt", "category": "heritage_culture", "region": "rajasthan", "destination": "Jaipur"}
    ),

    # ── RAJASTHAN (STATE) ──────────────────────────────────────────────────
    Document(
        page_content=(
            "Rajasthan is India's largest state and a land of royal forts, vibrant culture, and desert landscapes. "
            "Udaipur (City of Lakes): Lake Pichola with City Palace, Lake Palace Hotel (floating), Jagdish Temple. Romantic, best for couples. "
            "Jodhpur (Blue City): Mehrangarh Fort (most impressive fort in India), Jaswant Thada, Toorji Ka Jhalra stepwell. "
            "Jaisalmer (Golden City): Living fort in the Thar Desert, camel safaris, Sam Sand Dunes — overnight camping is unforgettable. "
            "Pushkar: Sacred lake with 400 ghats, Brahma Temple (one of very few in world), Pushkar Camel Fair in November (extraordinary event). "
            "Ranthambore National Park: Best tiger safari destination in India — jeep safaris and canter rides. "
            "Birla Temple Jaipur: modern marble temple, great for spiritual seekers. "
            "Best time to visit Rajasthan: October to March. Summers can hit 48°C in Jaisalmer. "
            "Rajasthan has excellent train connectivity — Palace on Wheels luxury train is iconic. "
            "Cultural highlights: Kalbelia dance, Manganiyar folk music, block printing workshops in Bagru."
        ),
        metadata={"source": "rajasthan_state_guide.txt", "category": "state_overview", "region": "rajasthan", "destination": "Rajasthan"}
    ),

    # ── RAJASTHAN FOOD ─────────────────────────────────────────────────────
    Document(
        page_content=(
            "Rajasthan cuisine is predominantly vegetarian and shaped by the desert environment. "
            "Dal Baati Churma is the quintessential dish — hard wheat rolls baked over fire, served with five-lentil dal and sweet churma. "
            "Gatte ki sabzi — gram flour dumplings in spiced yoghurt curry. "
            "Ker Sangri — desert bean and berry pickle-curry, unique to Rajasthan. "
            "Laal Maas — fiery red mutton curry, a non-vegetarian specialty. "
            "Ghewar — disc-shaped latticed sweet, popular during Teej and Gangaur festivals. "
            "Mawa Kachori (Jodhpur) — sweet kachori filled with mawa and dry fruits. "
            "Mirchi Bada — large green chilli fritter, street food staple in Jodhpur. "
            "Most forts require significant walking (2–3 hours visit). "
            "Vegetarian food is widely available; many restaurants are fully vegetarian. "
            "Rajasthani thali at restaurants like Chokhi Dhani (Jaipur) offers the full experience with folk performances."
        ),
        metadata={"source": "rajasthan_food_culture.txt", "category": "food_culture", "region": "rajasthan", "destination": "Rajasthan"}
    ),

    # ── MUMBAI ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Mumbai is India's financial capital and most cosmopolitan city, a city of dreams and extreme contrasts. "
            "Gateway of India — iconic colonial arch on the harbour, great starting point. "
            "Elephanta Caves — UNESCO World Heritage Site, accessible by ferry from Gateway of India (1 hr each way). Rock-cut caves with magnificent Shiva sculptures. "
            "Marine Drive (Queen's Necklace) — seafront promenade, spectacular at night. "
            "Bandra-Worli Sea Link and Bandra Fort for views. "
            "Dharavi — Asia's largest slum, many tour operators offer responsible tours. "
            "Chhatrapati Shivaji Maharaj Terminus (CST) — UNESCO heritage Victorian Gothic railway station. "
            "Colaba Causeway for shopping; Crawford Market for wholesale goods. "
            "Best time to visit: November to February. Avoid June–September monsoon (heavy flooding). "
            "Street food: Vada Pav (Mumbai's burger), Pav Bhaji, Bhel Puri, Misal Pav. "
            "Local trains are the lifeline but extremely crowded during rush hours. Autos and cabs (Ola/Uber) available. "
            "Best beaches: Juhu, Versova (less crowded), Aksa. "
            "Bollywood studio tours: Film City in Goregaon (pre-booking required)."
        ),
        metadata={"source": "mumbai_guide.txt", "category": "city_guide", "region": "west_india", "destination": "Mumbai"}
    ),

    # ── GOA ────────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Goa is India's smallest state and most popular beach destination, famous for its Portuguese heritage, beaches, and nightlife. "
            "North Goa beaches: Calangute, Baga, Anjuna, Vagator — lively, touristy, with beach shacks and water sports. "
            "Anjuna flea market every Wednesday is iconic. "
            "South Goa beaches: Palolem, Colva, Agonda, Butterfly Beach — quieter, cleaner, more serene. Palolem is the most beautiful. "
            "Peak season: December–January — most crowded and expensive; book accommodation months ahead. "
            "Shoulder season: October–November and February–March — pleasant weather, fewer crowds, good rates. "
            "Avoid June to September (monsoon) — most beach shacks close. "
            "Old Goa churches: Basilica of Bom Jesus (holds St. Francis Xavier's tomb), Se Cathedral — UNESCO World Heritage Sites. "
            "Dudhsagar Waterfalls (60 km east) — one of India's tallest waterfalls; jeep tour from Mollem. "
            "Food specialties: Goan Fish Curry, Pork Vindaloo, Prawn Balchão, Bebinca (layered dessert). "
            "Popular vegetarian restaurants: Bean Me Up (Anjuna), Navtara. "
            "Budget: Palolem has affordable guesthouses from ₹800/night. Luxury: Taj Exotica, W Goa."
        ),
        metadata={"source": "goa_guide.txt", "category": "beach_leisure", "region": "west_india", "destination": "Goa"}
    ),

    # ── KERALA ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Kerala, God's Own Country, is famous for its backwaters, hill stations, Ayurveda, and elephant festivals. "
            "Alleppey (Alappuzha) backwaters: Houseboat cruises on Kerala's network of lakes and canals are a must-do. "
            "Most houseboats are wheelchair accessible and suitable for elderly travelers. "
            "Book houseboats in advance for December–January peak. Day cruises from ₹5,000; overnight from ₹8,000. "
            "Munnar hill station: Tea plantations, Eravikulam National Park (Nilgiri Tahr), Top Station for views. "
            "Kochi (Cochin): Fort Kochi with Chinese fishing nets, Jew Town and Paradesi Synagogue, Kerala Kathakali performances, "
            "St. Francis Church (first European church in India), Mattancherry Palace. "
            "Thiruvananthapuram (Trivandrum): Padmanabhaswamy Temple (one of the richest temples), "
            "Kovalam Beach (lighthouse beach popular with foreigners). "
            "Wayanad: Wildlife sanctuary, Edakkal Caves (prehistoric drawings), trekking, coffee and spice plantations. "
            "Best time: September–March. Monsoon (June–August) is beautiful (Onam festival) but outdoor activities impacted. "
            "Ayurveda wellness: Kerala is the best place in India for authentic panchakarma treatments. "
            "Food: Kerala fish curry (red), Puttu and Kadala, Appam with Stew, Kerala Sadhya (banana-leaf feast)."
        ),
        metadata={"source": "kerala_guide.txt", "category": "backwaters_nature", "region": "south_india", "destination": "Kerala"}
    ),

    # ── BANGALORE (BENGALURU) ──────────────────────────────────────────────
    Document(
        page_content=(
            "Bengaluru (Bangalore) is India's Silicon Valley and a city of pleasant weather, craft beer, and parks. "
            "Lalbagh Botanical Garden — 240-acre garden with a glasshouse, flower shows, and a 3,000-million-year-old rock. "
            "Cubbon Park — 300-acre park in the city centre, great for morning walks. "
            "Bangalore Palace — Tudor-style palace with interesting interiors. "
            "ISKCON Temple Bengaluru — one of the largest ISKCON temples, beautifully illuminated at night. "
            "Vidhana Soudha — impressive government building, best photographed at night. "
            "UB City and Church Street for shopping and dining. "
            "Nandi Hills (60 km) — sunrise viewpoint, cycling paradise. "
            "Coorg (Kodagu, 250 km) — misty coffee estates, Abbey Falls, Raja's Seat, Dubare Elephant Camp. "
            "Mysore (145 km): Mysore Palace (stunning at night on Sundays), Chamundi Hills, Mysore Zoo, KRS Dam. "
            "Hampi (350 km): UNESCO heritage ruined Vijayanagara Empire capital — extraordinary boulder landscape with 1,600 monuments. "
            "Best time: September–February. Bengaluru has pleasant year-round weather (17–33°C). "
            "Food: MTR restaurant (iconic South Indian), craft beer at Toit and Arbor, Darshini joints for quick meals."
        ),
        metadata={"source": "bengaluru_guide.txt", "category": "city_guide", "region": "south_india", "destination": "Bangalore"}
    ),

    # ── HYDERABAD ──────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Hyderabad, the City of Pearls and Nizams, is a blend of Mughal grandeur and IT modernity. "
            "Charminar — iconic 16th-century mosque and monument; the symbolic face of Hyderabad. "
            "Golconda Fort — massive hilltop fort (11 km from city centre) with an impressive sound-and-light show in evenings. "
            "Ramoji Film City — one of the world's largest film studio complexes; popular family attraction with tours. "
            "Qutb Shahi Tombs — serene necropolis with seven rulers' domed tombs. "
            "Hussain Sagar Lake — large artificial lake with the Buddha Statue on a rock island (accessible by boat). "
            "Salar Jung Museum — one of India's largest museums, houses a vast collection of world art and artefacts. "
            "Birla Mandir and Birla Planetarium atop a hill. "
            "Laad Bazaar near Charminar — famous for lac bangles, pearls, and attar (perfume). "
            "Best time: October to February. Summers can hit 40°C. "
            "Food specialties: Hyderabadi Dum Biryani (must-try at Bawarchi or Paradise), Haleem (especially during Ramadan), "
            "Irani Chai, Osmania Biscuits, Qubani ka Meetha (apricot dessert). "
            "Telangana state tourism: Nagarjuna Sagar Dam (150 km) and Warangal forts worth day trips."
        ),
        metadata={"source": "hyderabad_guide.txt", "category": "city_guide", "region": "south_india", "destination": "Hyderabad"}
    ),

    # ── CHENNAI ────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Chennai (Madras) is Tamil Nadu's capital — an underrated cultural powerhouse of South India. "
            "Marina Beach — world's second-longest urban beach (13 km). Best at sunrise; avoid swimming (dangerous currents). "
            "Kapaleeshwarar Temple — a magnificent Dravidian temple with a towering gopuram in Mylapore. "
            "Fort St. George — oldest surviving British fort in India; houses the Tamil Nadu Legislative Assembly and a museum. "
            "Government Museum — one of India's best natural history and art collections. "
            "Mahabalipuram (Mamallapuram, 60 km): UNESCO World Heritage Shore Temple, cave temples, and stone chariot. Must-visit day trip. "
            "Kanchipuram (75 km): City of Thousand Temples, famous for silk sarees. "
            "Best time: November to February. Avoid October–November (north-east monsoon). "
            "Chennai is extremely hot April–June (34–42°C). "
            "Food: Chettinad cuisine (fiery spices), Idli-Sambar-Chutney, Filter Coffee, Banana Leaf meals. "
            "Pondy Bazaar and T Nagar for shopping; Express Avenue mall for modern retail. "
            "Chennai is a major hub for Carnatic classical music (December–January season at Music Academy)."
        ),
        metadata={"source": "chennai_guide.txt", "category": "city_guide", "region": "south_india", "destination": "Chennai"}
    ),

    # ── MYSORE ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Mysore (Mysuru) is Karnataka's cultural capital and one of India's cleanest and most regal cities. "
            "Mysore Palace — one of the largest palaces in India; spectacularly illuminated on Sunday evenings (7–7:45pm) and during Dasara festival. "
            "Entry: ₹100 Indians, ₹200 foreigners. Shoes must be removed, strict no-photography inside. "
            "Chamundi Hills — 13 km from city, temple atop hill with panoramic views; 1000-step staircase. "
            "Mysore Zoo — one of India's oldest and best-maintained zoos; excellent for families with kids. "
            "Brindavan Gardens (20 km, KRS Dam) — illuminated musical fountain evenings are popular. "
            "Srirangapatna (16 km) — Tipu Sultan's capital with his summer palace, fort, and gumbaz (tomb). "
            "Dasara Festival (Vijayadashami, September/October) — 10 days of royal celebrations, the most famous festival in the city. "
            "Best time: October to February. "
            "Mysore is famous for sandalwood products, Mysore Pak (ghee-based sweet), and silk sarees (Silk Emporium in Mysore). "
            "Coorg (Kodagu, 120 km) is easily accessible for a day or overnight coffee estate experience."
        ),
        metadata={"source": "mysore_guide.txt", "category": "heritage_culture", "region": "south_india", "destination": "Mysore"}
    ),

    # ── KOLKATA ────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Kolkata (Calcutta) is West Bengal's capital — India's intellectual and cultural powerhouse, city of Nobel laureates and artists. "
            "Victoria Memorial — stunning white marble colonial monument and museum; must-visit. "
            "Howrah Bridge — iconic cantilever bridge over the Hooghly River; most photographed at night. "
            "Dakshineswar Kali Temple and Belur Math (Ramakrishna Mission headquarters) across the river — peaceful spiritual sites. "
            "Indian Museum — one of Asia's oldest and largest museums. "
            "Marble Palace — eccentric 19th-century mansion with art collection; free entry with written permission. "
            "College Street (Boi Para) — world's largest second-hand book market. "
            "Kumartuli — potter's district where clay idols are made for Durga Puja. "
            "Durga Puja (September/October) — the world's biggest outdoor arts festival; Kolkata transforms into an open-air gallery. "
            "Best time: October to February. Monsoon (June–September) is uncomfortable due to heat-humidity. "
            "Food: Rosogolla (sweet), Mishti Doi (sweet yoghurt), Kati Roll, Prawn Malai Curry, Kosha Mangsho (dry mutton). "
            "Tram rides in old Kolkata are heritage experiences. Yellow Ambassador taxis are iconic."
        ),
        metadata={"source": "kolkata_guide.txt", "category": "city_guide", "region": "east_india", "destination": "Kolkata"}
    ),

    # ── DARJEELING ─────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Darjeeling in West Bengal is the Queen of the Hills — famous for tea gardens, the Himalayan view, and the iconic toy train. "
            "Tiger Hill sunrise viewpoint offers a stunning panoramic view of Kanchenjunga and, on clear days, even Everest. "
            "Darjeeling Himalayan Railway (Toy Train) — UNESCO World Heritage narrow-gauge steam railway. "
            "Tea estate tours: Happy Valley Tea Estate and Makaibari Tea Estate offer informative tours and tastings. "
            "Batasia Loop — circular railway loop with a war memorial and Kanchenjunga views. "
            "Himalayan Mountaineering Institute and Padmaja Naidu Himalayan Zoological Park (red panda, snow leopard) — great for families. "
            "Rock Garden and Ganga Maya Park for nature walks. "
            "Best time: April to June (spring flowers) and September to November (clearest Himalaya views). "
            "Avoid July–August (heavy monsoon fog and landslides). "
            "Cold weather: October–March can be below 5°C; pack heavy woolens. "
            "Accommodation: budget: Andy's Guest House, mid-range: Windamere Hotel (heritage), luxury: Glenburn Tea Estate. "
            "From Siliguri: NJP railway + shared jeep (3 hrs) or toy train (7 hrs scenic)."
        ),
        metadata={"source": "darjeeling_guide.txt", "category": "hill_station", "region": "east_india", "destination": "Darjeeling"}
    ),

    # ── GOA ACCESSIBILITY & FOOD ───────────────────────────────────────────
    Document(
        page_content=(
            "Goa's beaches are bustling during December and January, which are the peak crowd times. "
            "For a quiet trip, consider South Goa beaches like Palolem, Agonda, and Butterfly Beach. "
            "Popular vegetarian restaurants include 'Bean Me Up' (Anjuna) and 'Navtara'. "
            "Wheelchair accessibility: Most hotels have accessible rooms. Beach wheelchairs are available at select North Goa beaches. "
            "The flat terrain of Goa makes it good for mobility-impaired travelers compared to hill stations. "
            "Water sports: parasailing, jet skiing, banana boat rides, scuba diving (Grande Island) in North Goa. "
            "Cashew feni is the local spirit made from cashew apples; port wine is popular in Old Goa. "
            "Spice plantations in Ponda (30 km from Panaji) offer guided tours with elephant washing experiences. "
            "Casa Areia (Latin Quarter), Fontainhas in Panaji — colourful Portuguese heritage neighbourhood, UNESCO tentative list. "
            "Wildlife Sanctuary: Bhagwan Mahavir Wildlife Sanctuary in Mollem; Dudhsagar Falls is inside this sanctuary."
        ),
        metadata={"source": "goa_accessibility_food.txt", "category": "accessibility_food", "region": "west_india", "destination": "Goa"}
    ),

    # ── DELHI ACCESSIBILITY & INDOOR OPTIONS ──────────────────────────────
    Document(
        page_content=(
            "Delhi has severe air pollution issues in November and December (AQI can exceed 400–500). "
            "Historical sites like the Red Fort and Qutub Minar are completely outdoors. "
            "If air quality is bad, the National Museum is an excellent indoor alternative that takes 3–4 hours to explore. "
            "Lotus Temple is fully accessible for wheelchair users with ramps and accessible pathways throughout. "
            "India Gate is an outdoor space; evening visits are better air-wise on clear days. "
            "Safdarjung's Tomb and Humayun's Tomb have paved paths — manageable for those with limited mobility. "
            "Qutub Minar has uneven cobblestone paths; not recommended for wheelchair users. "
            "Handicraft hubs: Dilli Haat (fixed-price craft market from all states), Janpath Market, Khan Market. "
            "Delhi street food safety tips: eat at busy stalls with high turnover; avoid cut fruits and pre-mixed juices. "
            "Connaught Place area has many clean, affordable restaurants across all cuisines."
        ),
        metadata={"source": "delhi_accessibility_indoor.txt", "category": "accessibility_weather", "region": "north_india", "destination": "Delhi"}
    ),

    # ── KERALA ACCESSIBILITY ───────────────────────────────────────────────
    Document(
        page_content=(
            "Kerala is famous for its backwaters in Alleppey (Alappuzha). Houseboat cruises are a must-do experience. "
            "Most houseboats are wheelchair accessible, making it great for elderly travelers and those with mobility issues. "
            "The monsoon season (June to August) is beautiful but means outdoor activities might be rained out. "
            "The lush green landscape is spectacular during monsoon. "
            "Ayurveda treatments are highly recommended in Kerala — many resorts offer Panchakarma, Shirodhara, and rejuvenation packages. "
            "Periyar Tiger Reserve (Thekkady): Boat ride on Periyar Lake to spot elephants and wildlife. "
            "Munnar is at 1,600m altitude — can be cold and foggy; not ideal for those with respiratory issues. "
            "Varkala beach has red laterite cliffs and is less crowded than Goa. Mineral springs at the cliff base. "
            "Theyyam ritual performances in North Kerala (October–May) are extraordinary cultural experiences. "
            "Kerala is generally safe for solo female travelers. "
            "Budget stays: homestays and lodges from ₹800. Luxury: Kumarakom Lake Resort, Spice Village."
        ),
        metadata={"source": "kerala_accessibility.txt", "category": "accessibility_wellness", "region": "south_india", "destination": "Kerala"}
    ),

    # ── HIMACHAL PRADESH ───────────────────────────────────────────────────
    Document(
        page_content=(
            "Himachal Pradesh is the premier Himalayan state for adventure and scenic beauty in northern India. "
            "Shimla: Former British summer capital — Mall Road, Christ Church, Viceregal Lodge. Best October–June. "
            "Avoid December–January snow unless skiing. Kalka-Shimla Railway is UNESCO heritage. "
            "Manali: Hub for Rohtang Pass (snow), Solang Valley (adventure sports), Hadimba Devi Temple, Old Manali village. "
            "Rohtang Pass requires a permit (apply online). Open June–October. "
            "Dharamshala / McLeod Ganj: Home of the Dalai Lama and Tibetan government-in-exile. "
            "Namgyal Monastery, Norbulingka Institute, Bhagsu Waterfall, Triund trek (7 km). "
            "Spiti Valley (cold desert): Key Monastery, Dhankar Fort, Chandratal Lake. Best July–September. "
            "Dalhousie: Colonial hill station, Kalatop Wildlife Sanctuary, Khajjiar (Mini Switzerland). "
            "Best time: May–June and September–October. Winter (Dec–Feb): only for skiing at Kufri or Solang. "
            "Para-gliding at Bir Billing is a world-class experience. "
            "Trekking: Hampta Pass, Pin Parvati, Beas Kund — various difficulty levels."
        ),
        metadata={"source": "himachal_pradesh_guide.txt", "category": "hill_station_adventure", "region": "north_india", "destination": "Himachal Pradesh"}
    ),

    # ── UTTARAKHAND ────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Uttarakhand is the 'Land of Gods' — a state of spiritual sites, Himalayan treks, and wildlife. "
            "Rishikesh: Yoga capital of the world. Triveni Ghat Aarti, Laxman Jhula, Ram Jhula suspension bridges. "
            "White water rafting on the Ganges (Grade III–IV). Beatles Ashram (Maharishi's ashram). "
            "Haridwar: One of seven sacred Hindu cities. Har Ki Pauri Ganga Aarti at dusk is spectacular. "
            "Kumbh Mela held here every 12 years (next in 2034) — world's largest human gathering. "
            "Mussoorie (Queen of Hills): Gun Hill, Kempty Falls, Lal Tibba viewpoint. Colonial hill station. "
            "Nainital: Lake district — Naini Lake boating, Snow View, Naina Devi Temple. Suitable for families. "
            "Auli: India's premier skiing destination (January–March). Summer: meadow views of Nanda Devi. "
            "Jim Corbett National Park: India's oldest national park; best tiger safari after Ranthambore. "
            "Char Dham Yatra: Yamunotri, Gangotri, Kedarnath, Badrinath — sacred pilgrimage circuit. "
            "Open May–June and September–October (Kedarnath higher altitude closes earlier). "
            "Valley of Flowers: UNESCO World Heritage meadow blooming with rare Himalayan flowers (August). "
            "Trek to Kedarnath (22 km): moderate-difficult; helicopter options available."
        ),
        metadata={"source": "uttarakhand_guide.txt", "category": "spiritual_adventure", "region": "north_india", "destination": "Uttarakhand"}
    ),

    # ── LADAKH ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Ladakh (Land of High Passes) is India's highest, most remote, and most dramatic landscape. "
            "Leh: Main town with Leh Palace (ruined but spectacular views), Shanti Stupa, Leh Market. "
            "Pangong Tso Lake: Famous blue-green lake at 4,350m altitude, touching China. Requires Inner Line Permit. "
            "Nubra Valley (150 km north of Leh): Khardung La Pass (world's highest motorable road at 5,359m), "
            "Diskit Monastery, double-humped Bactrian camels at Hunder Sand Dunes. "
            "Magnetic Hill: Optical illusion near Leh where vehicles appear to roll uphill. "
            "Key/Ki Monastery in Spiti (technically Himachal but nearby): Buddhist monastery at 4,166m. "
            "Tso Moriri Lake: Less visited than Pangong, equally stunning, fewer tourists. "
            "Best time to visit: June to September. All roads/airports closed mid-October to April. "
            "Altitude sickness: Acclimatise 1–2 days in Leh (3,500m) before heading higher. No alcohol or heavy exercise for first 48 hrs. "
            "Inner Line Permit required for Pangong, Nubra, Tso Moriri — obtainable in Leh. "
            "Permit for Rohtang (entering from Manali side): online only. "
            "Adventure: mountain biking on Khardung La, river rafting on Zanskar River."
        ),
        metadata={"source": "ladakh_guide.txt", "category": "adventure_remote", "region": "north_india", "destination": "Ladakh"}
    ),

    # ── JAMMU & KASHMIR ────────────────────────────────────────────────────
    Document(
        page_content=(
            "Jammu & Kashmir is paradise on earth — mountain lakes, Mughal gardens, and houseboats on Dal Lake. "
            "Srinagar: Dal Lake shikara rides and houseboat stays are iconic. "
            "Mughal Gardens: Shalimar Bagh, Nishat Bagh, Chashme Shahi — terraced Mughal gardens overlooking the lake. "
            "Pari Mahal (seven terraces) and Harwan Garden. "
            "Tulip Garden (March–April) — Asia's largest tulip garden, extraordinary. "
            "Gulmarg: India's top ski resort in winter. In summer: gondola cable car to Apharwat Peak (4,390m), meadows, golf. "
            "Pahalgam: Scenic valley with the Lidder River, base for Amarnath Yatra pilgrimage. "
            "Betaab Valley (named after the film) and Chandanwari (16 km) are popular. "
            "Sonamarg: Gateway to Zoji La; pony rides, glaciers, Thajiwas Glacier trek. "
            "Jammu: Vaishno Devi shrine (6 km trek from Katra, one of India's busiest pilgrimage sites). "
            "Best time: April–June (spring flowers) and September–October (autumn colours). "
            "Winters December–February for skiing in Gulmarg. Always check current travel advisories."
        ),
        metadata={"source": "kashmir_guide.txt", "category": "scenic_pilgrimage", "region": "north_india", "destination": "Kashmir"}
    ),

    # ── SIKKIM & NORTHEAST ─────────────────────────────────────────────────
    Document(
        page_content=(
            "Sikkim is a small Himalayan state bordering China, Bhutan, and Nepal — pristine, clean, and stunning. "
            "Gangtok: Rumtek Monastery (one of the largest in India), MG Marg pedestrian promenade, Enchey Monastery. "
            "Pelling: Kanchenjunga (world's third-highest peak) views, Rabdentse ruins, Pemayangtse Monastery, Khecheopalri Lake. "
            "North Sikkim: Yumthang Valley (Valley of Flowers, April–May), Gurudongmar Lake (sacred, 5,430m). Requires protected area permit. "
            "Yuksom: Starting point for Kanchenjunga Base Camp trek. "
            "Nathu La Pass (4,310m): India-China border; permit required from Gangtok; open to Indian citizens only. "
            "Permits: Protected Area Permit required for all of Sikkim for foreigners. "
            "Best time: March–May (rhododendron bloom) and October–November (clear Himalaya views). "
            "Meghalaya: Shillong (Scotland of the East), Cherrapunji/Sohra (wettest place on earth), Mawlynnong (Asia's cleanest village), "
            "Dawki (crystal-clear Umngot River), Living Root Bridges (UNESCO tentative). "
            "Assam: Kaziranga National Park (one-horned rhinoceros), Majuli (world's largest river island), Kamakhya Temple."
        ),
        metadata={"source": "northeast_india_guide.txt", "category": "nature_adventure", "region": "northeast_india", "destination": "Northeast India"}
    ),

    # ── ANDAMAN & NICOBAR ──────────────────────────────────────────────────
    Document(
        page_content=(
            "Andaman & Nicobar Islands offer some of Asia's best beaches, scuba diving, and untouched nature. "
            "Port Blair: Cellular Jail (Kaala Pani) — national memorial and sound-and-light show; harrowing history of political prisoners. "
            "Ross Island (now Netaji Subhash Chandra Bose Island): Ruins of former British headquarters, spotted deer. "
            "Havelock Island (Swaraj Dweep): Radhanagar Beach — repeatedly rated Asia's best beach. "
            "Neil Island (Shaheed Dweep): Quieter, pristine — Natural Bridge, Laxmanpur beach sunset. "
            "Scuba diving: World-class visibility and coral. Dive shops in Havelock. Best diving: November–April. "
            "Sea walk, glass-bottom boat tours, kayaking available for non-divers. "
            "North Bay Island (Coral Island): Snorkelling, sea walk, semi-submarine. "
            "Baratang Island: Limestone caves, mudvolcanoes (permit required from Port Blair). "
            "Permits: Inner Line Permit for Port Blair issued on arrival. Special permit needed for Nicobar. "
            "Best time: November to April. Avoid May–September (monsoon, rough seas, most trips cancelled). "
            "Ferries connect Port Blair to Havelock (2.5 hrs, government or private speedboat). "
            "Flight from Chennai/Kolkata/Delhi to Port Blair (2–3 hrs)."
        ),
        metadata={"source": "andaman_guide.txt", "category": "beach_diving", "region": "island_india", "destination": "Andaman"}
    ),

    # ── HAMPI ──────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Hampi is a UNESCO World Heritage Site in Karnataka — the magnificent ruins of the Vijayanagara Empire (14th–16th century). "
            "Over 1,600 archaeological sites spread across a stunning boulder-strewn landscape with the Tungabhadra River. "
            "Virupaksha Temple — the main living temple, still in active worship; the oldest and most important in Hampi. "
            "Vittala Temple Complex — the most iconic monument; the stone chariot is the symbol of Karnataka. "
            "The musical pillars in Vittala Temple produce musical notes when tapped. "
            "Royal Enclosure: Mahanavami Dibba (ceremonial platform), Hazara Rama Temple, Underground Shiva Temple. "
            "Matanga Hill — best sunrise viewpoint over Hampi; steep 30-minute climb. "
            "Anegundi (across the river) — older settlement with Hanuman's birthplace (Anjanei Hill). "
            "Exploring by bicycle or moped is the best way to cover the vast site. "
            "Coracle rides (round basket boats) on the Tungabhadra are a fun activity. "
            "Best time: October to February. Summers are extremely hot (40°C+). "
            "Rock climbing: The boulders of Hampi are a mecca for climbing enthusiasts. "
            "Most sites are open sunrise to sunset; entry fee applicable at Vittala Temple."
        ),
        metadata={"source": "hampi_guide.txt", "category": "heritage_archaeological", "region": "south_india", "destination": "Hampi"}
    ),

    # ── PONDICHERRY ────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Pondicherry (Puducherry) is a former French colony with a charming Franco-Tamil character. "
            "The French Quarter (White Town): Colourful heritage buildings, French war memorial, beachside promenade (Boulevard), "
            "Alliance Française, cafes and bistros. "
            "Auroville: International township 10 km away; the Matrimandir meditation dome is the centrepiece. "
            "Auroville Visitors Centre for information; free guided tour of the township on foot. "
            "Sri Aurobindo Ashram: Major spiritual centre in the heart of Pondicherry; serene and beautiful. "
            "Basilica of the Sacred Heart of Jesus: Impressive Gothic church. "
            "Paradise Beach: Accessible only by boat from Chunnambar; clean, less crowded beach. "
            "Serenity Beach: Popular for surfing lessons. "
            "Manakula Vinayagar Temple: Famous Ganesha temple in the French Quarter. "
            "Best time: October to March. Avoid April–June (very hot) and October–November (cyclone risk). "
            "Food: French patisseries, Tamil cuisine, Sri Aurobindo Ashram's bakery products. "
            "Cycling or walking is the best way to explore White Town. "
            "Pondicherry is 160 km from Chennai (3 hrs by bus or car)."
        ),
        metadata={"source": "pondicherry_guide.txt", "category": "heritage_spiritual", "region": "south_india", "destination": "Pondicherry"}
    ),

    # ── RANTHAMBORE TIGER SAFARI ───────────────────────────────────────────
    Document(
        page_content=(
            "Ranthambore National Park in Rajasthan is India's premier tiger reserve and one of the best places in the world to see tigers in the wild. "
            "The park has about 75 tigers. Tigers are frequently spotted near lakes and watering holes. "
            "Two safari options: Gypsy (open Jeep, 6 persons) and Canter (open bus, 20 persons). "
            "Gypsies offer better sighting opportunities; Canters are more economical. "
            "Morning safari (6–10 AM) and afternoon safari (2–6 PM). "
            "Booking: Online through Rajasthan tourism website; book at least 3–4 months in advance for peak season (October–April). "
            "Best time for tiger sightings: March–May (water scarcity drives tigers to open areas). "
            "Ranthambore Fort (inside the park) is a 10th-century fort on a hill with resident langurs and peacocks — UNESCO heritage. "
            "Besides tigers: leopards, sloth bears, jackals, hyenas, mugger crocodiles, 300+ bird species. "
            "Nearest base: Sawai Madhopur (12 km from park gate); trains from Jaipur (2 hrs) and Delhi (5 hrs). "
            "Budget: Safari ₹600–1,500 per person + park entry. Premium resort: Aman-i-Khás, The Oberoi Vanyavilas."
        ),
        metadata={"source": "ranthambore_guide.txt", "category": "wildlife_safari", "region": "rajasthan", "destination": "Ranthambore"}
    ),

    # ── KAZIRANGA ──────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Kaziranga National Park in Assam is a UNESCO World Heritage Site and home to the world's largest population of one-horned rhinoceroses. "
            "Over 2,400 Indian one-horned rhinos, 1,300 Asian elephants, 1,000 wild buffaloes, 120+ Royal Bengal Tigers. "
            "Safari options: Jeep safari and elephant-back safari. Elephant safaris are particularly good for rhino spotting at close range. "
            "Central Range is best for rhino and elephant sightings; Bagori Range for tigers. "
            "Best time: November to April. Park closes mid-April to mid-October (monsoon floods). "
            "Kohora is the main village near the park entrance. "
            "Majuli Island (65 km): World's largest river island in the Brahmaputra; known for Vaishnavite monasteries (satras). "
            "Guwahati: Kamakhya Temple (Shakti Peeth, major pilgrimage), Umananda Island Temple, Assam State Zoo. "
            "Assam silk (Muga and Eri) and famous Assam tea — plantation visits available near Jorhat. "
            "Assam cuisine: Assamese rice beer (Apong), Masor Tenga (fish curry), Pork with bamboo shoot."
        ),
        metadata={"source": "kaziranga_guide.txt", "category": "wildlife", "region": "northeast_india", "destination": "Kaziranga"}
    ),

    # ── GUJARAT ────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Gujarat is a diverse state of Rann of Kutch, Gir lions, Dwarka, and Ahmedabad's heritage. "
            "Ahmedabad: UNESCO Heritage City (first in India). Sabarmati Ashram (Gandhi's residence), "
            "Old City's pol (traditional neighbourhood), Sidi Saiyyed Mosque (intricate stone jaali lattice). "
            "Rann of Kutch (White Rann): Vast white salt desert. Rann Utsav (November–February) is the cultural festival; "
            "full moon nights are magical. Flamingoes, wild ass sanctuary nearby. "
            "Gir National Park: Only place in Asia with Asiatic Lions; safari required (book online). "
            "Dwarka: One of four holy dhams (Char Dham) — Dwarkadhish Temple on the sea. "
            "Somnath: First of 12 Jyotirlingas. Rebuilt temple at the seafront with sound-and-light show. "
            "Sasangir (near Gir): Traditional Siddi community descendants of African slaves. "
            "Champaner-Pavagadh: UNESCO heritage site with mosques, temples, palaces. "
            "Best time: October to March. "
            "Food: Dhokla, Thepla, Undhiyu (winter), Fafda-Jalebi (breakfast). Pure vegetarian state — alcohol banned."
        ),
        metadata={"source": "gujarat_guide.txt", "category": "state_overview", "region": "west_india", "destination": "Gujarat"}
    ),

    # ── MADHYA PRADESH ─────────────────────────────────────────────────────
    Document(
        page_content=(
            "Madhya Pradesh is the 'Heart of India' — a treasure house of UNESCO World Heritage Sites and national parks. "
            "Khajuraho: UNESCO heritage temples with erotic sculptures from the Chandela dynasty (10th–11th century). "
            "The Western Group of Temples is the main complex; Kandariya Mahadeva Temple is the largest. "
            "Sound-and-light show evenings at Khajuraho. "
            "Bhopal: Bharat Bhavan (art centre), Van Vihar National Park, Sanchi Stupa (50 km) — UNESCO Buddhist heritage, "
            "Bhojpur Temple (unfinished Shiva temple), tribal museums. "
            "Pachmarhi (1,067m): Only hill station in MP, beautiful waterfalls and rock paintings. "
            "Orchha: Pristine gem — medieval Rajput temples, palaces, cenotaphs, and the Betwa River. "
            "Gwalior: Gwalior Fort (one of India's finest), Jai Vilas Palace, Tansen's tomb (music tradition). "
            "Tiger Parks: Kanha (best for tigers, hardground), Bandhavgarh (highest tiger density), Pench. "
            "Ujjain: Sacred city on Kshipra River, Mahakaleshwar Jyotirlinga, Kumbh Mela every 12 years. "
            "Best time: October to March. Park season: October to June."
        ),
        metadata={"source": "madhya_pradesh_guide.txt", "category": "state_overview", "region": "central_india", "destination": "Madhya Pradesh"}
    ),

    # ── AMRITSAR ───────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Amritsar in Punjab is the spiritual and cultural heart of Sikhism. "
            "Harmandir Sahib (Golden Temple): The holiest shrine in Sikhism, covered in gold. "
            "Open 24 hours, free entry. The langar (community kitchen) feeds 100,000+ people daily for free — a profound experience. "
            "Dress code: Cover head (scarves/bandanas provided), remove shoes, wash feet at the entrance. "
            "Best time to visit: Pre-dawn (3–5 AM) for the most spiritual atmosphere, or Amrit Vela. "
            "Wagah Border ceremony: 30 km from Amritsar. The evening flag-lowering ceremony with India-Pakistan parade is electrifying. "
            "Best time to arrive: 3 PM (gates open 4 PM). "
            "Jallianwala Bagh: Garden and memorial site of the 1919 massacre; deeply moving. "
            "Partition Museum: World's first museum on the 1947 Partition; extremely moving. "
            "Food: Amritsar is a food paradise. Makhan Fish and Chicken Corner (famous for Amritsari fish), "
            "Kesar Da Dhaba (iconic dal makhani since 1916), Kulcha-Chole at Bhai Kulwant Singh Ji. "
            "Best time: October to March. Baisakhi (April 13–14) is the harvest and Sikh new year festival. "
            "Delhi to Amritsar: Shatabdi Express (5.5 hrs); direct flights also available."
        ),
        metadata={"source": "amritsar_guide.txt", "category": "spiritual_cultural", "region": "north_india", "destination": "Amritsar"}
    ),

    # ── OOTY ───────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Ooty (Udhagamandalam) is the Queen of Hill Stations in Tamil Nadu, located in the Nilgiri Hills at 2,240m altitude. "
            "Nilgiri Mountain Railway (Toy Train): UNESCO World Heritage rack railway from Mettupalayam to Ooty; a spectacular 5-hour journey through misty forests and tea estates. "
            "Book well in advance on IRCTC website. "
            "Government Botanical Garden: 55-acre garden with 1,000+ plant species; Tree Lover's Park. "
            "Ooty Lake: Boating and horse riding popular with families. "
            "Doddabetta Peak (2,637m): Highest point in Nilgiris with telescope point. "
            "Pykara Lake and Falls: 19 km from Ooty; boat rides, beautiful scenery. "
            "Mudumalai National Park (60 km): Elephant sightings, jeep safaris. Part of the Nilgiri Biosphere Reserve. "
            "Best time: April to June (spring) and September to November. "
            "Avoid December–January (too cold and foggy) and monsoon (July–August). "
            "Local produce: Nilgiri tea, Toda tribal embroidery, homemade chocolate shops on Commercial Road. "
            "Kodaikanal (another nearby hill station): Kodai Lake, Pillar Rocks, Berijam Lake; quieter than Ooty."
        ),
        metadata={"source": "ooty_guide.txt", "category": "hill_station", "region": "south_india", "destination": "Ooty"}
    ),

    # ── INDIAN TEMPLES OVERVIEW ────────────────────────────────────────────
    Document(
        page_content=(
            "India is home to some of the world's most spectacular temples, spanning diverse architectural styles and religious traditions. "
            "North Indian (Nagara) style: Khajuraho temples (MP), Somnath and Dwarka (Gujarat), Varanasi temples (UP). "
            "South Indian (Dravidian) style: Brihadeeswarar Temple Thanjavur, Meenakshi Amman Temple Madurai, "
            "Shore Temple Mahabalipuram, Virupaksha Temple Hampi. "
            "Cave temples: Ellora Caves (both Hindu and Buddhist, UNESCO), Ajanta Caves (Buddhist paintings, UNESCO), "
            "Elephanta Caves (Shiva sculptures, UNESCO). "
            "12 Jyotirlingas (sacred Shiva shrines): Somnath (Gujarat), Mallikarjuna (AP), Mahakaleshwar (MP), "
            "Omkareshwar (MP), Kedarnath (Uttarakhand), Bhimashankar (Maharashtra), Kashi Vishwanath (UP), "
            "Trimbakeshwar (Maharashtra), Vaidyanath (Jharkhand), Nageshvara (Gujarat), Rameshwaram (Tamil Nadu), Grishneshwar (Maharashtra). "
            "Char Dham (4 holy pilgrimage sites): Badrinath (Uttarakhand), Puri Jagannath (Odisha), "
            "Dwarka (Gujarat), Rameshwaram (Tamil Nadu). "
            "Major Buddhist sites: Bodh Gaya (Bihar) — where Buddha attained enlightenment; Sarnath (UP) — first sermon; "
            "Ajanta (Maharashtra) — painted caves; Nalanda (Bihar) — ancient university ruins."
        ),
        metadata={"source": "india_temples_overview.txt", "category": "temples_pilgrimage", "region": "all_india", "destination": "India"}
    ),

    # ── INDIAN WILDLIFE SANCTUARIES ────────────────────────────────────────
    Document(
        page_content=(
            "India has 106 National Parks and 551 Wildlife Sanctuaries. Key destinations for wildlife enthusiasts: "
            "Ranthambore (Rajasthan): Best for tiger sightings; October–June season. "
            "Jim Corbett (Uttarakhand): India's oldest national park; tigers, elephants, 600 bird species. "
            "Kaziranga (Assam): One-horned rhinoceros capital; November–April. "
            "Kanha (MP): Inspiration for Jungle Book; barasingha (swamp deer), tigers; October–June. "
            "Bandhavgarh (MP): Highest tiger density; white tigers historically found here. "
            "Periyar (Kerala): Boat rides to spot elephants; spice plantation visits. "
            "Gir (Gujarat): Only wild Asiatic Lions. "
            "Sundarbans (West Bengal): Mangrove delta, Bengal tigers — boat safaris; best Nov–Feb. "
            "Nagarhole / Kabini (Karnataka): Leopards, elephants, tigers, dholes (wild dogs); beautiful river scenery. "
            "Panna (MP): Project Tiger reserve; boat trips to spot gharials (freshwater crocodilians). "
            "Bharatpur (Rajasthan): Keoladeo Ghana Bird Sanctuary — UNESCO heritage; wintering birds from Central Asia. "
            "Best time for most parks: October–April. Many close during monsoon (June–September). "
            "Safari bookings: Required online for most parks; Gypsy/Jeep preferred for sightings."
        ),
        metadata={"source": "india_wildlife_guide.txt", "category": "wildlife_safari", "region": "all_india", "destination": "India Wildlife"}
    ),

    # ── INDIA TREKKING DESTINATIONS ────────────────────────────────────────
    Document(
        page_content=(
            "India offers world-class trekking destinations from the Himalayas to the Western Ghats. "
            "Chadar Trek (Ladakh): Frozen Zanskar River trek in January–February. Extremely challenging. "
            "Roopkund Trek (Uttarakhand): Mystery Lake at 5,029m with skeletal remains. September best. "
            "Valley of Flowers (Uttarakhand): UNESCO trek through alpine meadows. July–August bloom. "
            "Goechala Trek (Sikkim): Views of Kanchenjunga from 4,940m. October–November. "
            "Markha Valley Trek (Ladakh): High-altitude desert trek; June–October. "
            "Triund Trek (Dharamshala, HP): Easy 7 km trek, popular for beginners; open year-round. "
            "Kedarkantha (Uttarkashi, UK): Winter snow trek; December–April. Stunning 360° views. "
            "Hampta Pass (Manali, HP): Crosses from Kullu to Spiti; moderate; July–September. "
            "Sandakphu Trek (West Bengal/Sikkim): Highest peak in West Bengal; views of 4 of 5 highest peaks. "
            "Kumara Parvatha (Karnataka): 13 km through dense Pushpagiri forest; moderate; October–February. "
            "Western Ghats: Chembra Peak (Kerala), Mullayanagiri (highest in Karnataka), Anamudi (highest south of Himalayas in Kerala). "
            "Key tips: Register with forest department. Go with a certified guide for high-altitude treks. Altitude sickness prevention is key above 3,000m."
        ),
        metadata={"source": "india_trekking_guide.txt", "category": "trekking_adventure", "region": "all_india", "destination": "India Trekking"}
    ),

    # ── INDIA BEACHES ──────────────────────────────────────────────────────
    Document(
        page_content=(
            "India has 7,500 km of coastline with world-class beaches on three sides. "
            "Best beaches in India: "
            "Radhanagar Beach, Havelock (Andaman): Rated Asia's best. White sand, turquoise water, lush forest backdrop. "
            "Palolem, South Goa: Crescent-shaped, calm waters, beautiful. "
            "Agonda, South Goa: Quieter than Palolem, nesting Olive Ridley turtles. "
            "Varkala, Kerala: Red laterite cliff beaches, mineral springs, yoga retreats. "
            "Kovalam, Kerala: Three coves with lighthouse; popular with foreign tourists. "
            "Om Beach, Gokarna (Karnataka): Hippie alternative to Goa; less commercial. "
            "Tarkarli and Malvan (Maharashtra): Scuba diving, clear water. "
            "Marina Beach, Chennai: World's second-longest; beautiful at dawn; swimming dangerous. "
            "Lakshadweep: Coral atolls with pristine beaches; very restricted access (permit needed). "
            "Best beaches for families: Calangute (Goa), Kovalam, Rushikonda (Vizag). "
            "Best for water sports: Baga/Calangute (Goa), Neil Island (Andaman). "
            "Best for seclusion: Butterfly Beach (Goa — boat access only), Bangaram Island (Lakshadweep). "
            "Avoid east coast beaches (Visakhapatnam, Puri) during June–October cyclone season."
        ),
        metadata={"source": "india_beaches_guide.txt", "category": "beaches", "region": "all_india", "destination": "India Beaches"}
    ),

    # ── INDIA FOOD & CUISINE ───────────────────────────────────────────────
    Document(
        page_content=(
            "Indian cuisine is extraordinarily diverse — each state has its own distinct culinary tradition. "
            "North India: Butter Chicken, Dal Makhani, Chole Bhature, Paranthe, Biryani (Lucknow/Hyderabad styles). "
            "Rajasthan: Dal Baati Churma, Gatte ki Sabzi, Laal Maas. "
            "Gujarat: Dhokla, Thepla, Undhiyu, Gujarati Thali (sweet-sour-spicy balance). "
            "Maharashtra: Vada Pav, Misal Pav, Puran Poli, Kolhapuri Chicken. "
            "Karnataka: Bisi Bele Bath, Ragi Mudde, Mysore Masala Dosa, Coorg Pork Curry. "
            "Tamil Nadu: Chettinad Chicken, Idli-Sambar, Rasam, Banana Leaf Meals, Filter Coffee. "
            "Kerala: Fish Moilee, Appam with Stew, Puttu-Kadala, Prawn Mango Curry. "
            "Bengal: Rosogolla, Mishti Doi, Hilsa fish (Ilish), Kosha Mangsho, Maacher Jhol. "
            "Odisha: Dalma (lentil-vegetable), Chhena Poda (baked cottage cheese sweet). "
            "Northeast India: Bamboo shoot dishes, Pork with sesame, Assam tea. "
            "Street food musts: Pani Puri (everywhere), Kati Roll (Kolkata), Dahi Vada, Pav Bhaji (Mumbai), Chaat. "
            "Vegetarian travel note: India is one of the best countries for vegetarians — most states have excellent vegetarian options. "
            "Jain food (no onion/garlic) widely available in Gujarat and Rajasthan."
        ),
        metadata={"source": "india_food_guide.txt", "category": "food_culture", "region": "all_india", "destination": "India Food"}
    ),

    # ── INDIA TRAVEL TIPS ─────────────────────────────────────────────────
    Document(
        page_content=(
            "Essential travel tips for visiting India: "
            "Visa: Most nationalities need e-Visa (apply online 3–4 days before); available for 30/90 days. "
            "Currency: Indian Rupee (INR). ATMs widely available; carry some cash in smaller towns. "
            "Transport: Trains are the backbone of Indian travel. Book on IRCTC website (create account). "
            "Tatkal quota allows last-minute bookings (higher fare). "
            "Domestic flights: IndiGo, Air India, Vistara, SpiceJet are main airlines. "
            "Health: Consult doctor about typhoid, hepatitis A vaccinations. Drink only bottled or filtered water. "
            "Food safety: Eat at busy restaurants; avoid cut fruit, roadside juices in unhygienic conditions. "
            "Monsoon (June–September): Most of India is wet; hill stations see landslides; some parks close. "
            "Dressing: Dress modestly at religious sites (temples, mosques, gurudwaras). Carry a scarf. "
            "Photography: Ask permission before photographing people. No photography inside some temples and museums. "
            "Bargaining: Expected at markets and with auto-rickshaws where meters are not used. "
            "SIM card: Buy a prepaid SIM at the airport (Airtel, Jio are best). Requires passport and photo. "
            "Scams: Be aware of taxi/guide overcharging at tourist spots. Use apps like Ola/Uber for fair pricing. "
            "Tipping: Not mandatory but appreciated; 10% at restaurants is good practice."
        ),
        metadata={"source": "india_travel_tips.txt", "category": "travel_tips", "region": "all_india", "destination": "India"}
    ),

    # ── INDIA BUDGET TRAVEL ───────────────────────────────────────────────
    Document(
        page_content=(
            "India is a budget traveler's paradise with excellent value for money. "
            "Budget accommodation (₹300–1,500/night): Hostels (Zostel chain), government tourist bungalows, dharamshalas near temples, budget guesthouses. "
            "Mid-range (₹2,000–6,000/night): Business hotels, heritage havelis, comfortable resorts. "
            "Luxury (₹8,000–50,000+/night): Palace hotels (Taj, Oberoi, ITC), luxury tented camps, private wildlife lodges. "
            "Food budgets: Street food meals from ₹50–200. Restaurant meals from ₹150–600. Fine dining ₹1,000+. "
            "Transport costs: Auto-rickshaw app rides ₹30–200. Local bus ₹5–50. Sleeper train tickets ₹200–800. "
            "Entry fees: Most Indian temples are free. Museums ₹20–500. Heritage sites ₹50–1,100 (foreign rates higher). "
            "Haggling: Acceptable at markets, auto-rickshaws (if no meter/app), local shops. Fixed price signs mean no haggling. "
            "Free experiences: Ganga Aarti (Varanasi/Haridwar), langar at gurudwaras, many beaches, most parks/ghats. "
            "Seasonal rates: Rajasthan hotels cost 30–50% less in summer. Goa is cheapest in early monsoon. "
            "Budget travel hubs: Varanasi, Pushkar, Rishikesh, McLeod Ganj — well-developed backpacker infrastructure."
        ),
        metadata={"source": "india_budget_travel.txt", "category": "budget_travel", "region": "all_india", "destination": "India Budget"}
    ),

    # ── INDIA HIGH BUDGET / LUXURY TRAVEL ─────────────────────────────────
    Document(
        page_content=(
            "India offers extraordinary luxury travel experiences — palaces, private wildlife safaris, and bespoke itineraries. "
            "Palace hotels: Rambagh Palace (Jaipur), Umaid Bhawan Palace (Jodhpur), The Leela Palace (multiple cities), "
            "Taj Lake Palace (Udaipur, island hotel on Lake Pichola), Samode Palace (near Jaipur). "
            "Luxury train journeys: Palace on Wheels (Rajasthan, 8 days), Maharajas Express (all-India, 8–15 days), "
            "Golden Chariot (South India). These are among the world's most luxurious train experiences. "
            "Private wildlife safari lodges: Aman-i-Khás at Ranthambore, The Oberoi Vanyavilas at Ranthambore, "
            "Taj Safaris at Bandhavgarh and Panna. "
            "Houseboat luxury: Kettuvallam houseboats in Kerala from ₹20,000–50,000/night. Premium: CGH Earth's Coconut Lagoon resort. "
            "Ayurveda retreats: Kairali Ayurvedic Health Village (Kerala), Indus Valley Ayurvedic Centre (Mysore). "
            "Cooking classes: Rajasthan heritage haveli cooking, Kerala spice class, street food tours in Delhi. "
            "Private guides: Available at all major heritage sites; excellent for deeper historical context (₹1,500–4,000/day). "
            "Helicopter tours: Over Taj Mahal (Agra), Char Dham (Uttarakhand), Leh valley."
        ),
        metadata={"source": "india_luxury_travel.txt", "category": "luxury_travel", "region": "all_india", "destination": "India Luxury"}
    ),

    # ── INDIA SEASONAL GUIDE ──────────────────────────────────────────────
    Document(
        page_content=(
            "India seasonal travel guide — knowing when to visit is critical: "
            "October to March (Winter/Dry Season): Best time for most of India. "
            "- Rajasthan, Delhi, Agra: Pleasant 10–25°C. "
            "- Kerala, Goa, Karnataka, Tamil Nadu: Excellent beach and backwater weather. "
            "- Himalayas (lower altitudes): Clear skies, cold nights but manageable. "
            "April to June (Pre-Monsoon/Summer): "
            "- Plains: Very hot (35–45°C). Avoid Delhi, Agra, Rajasthan in May–June. "
            "- Best: Hill stations (Shimla, Manali, Darjeeling, Ooty) — peak tourist season. "
            "- Ladakh, Spiti, Zanskar Valley open June onwards. "
            "June to September (Monsoon): "
            "- Most of India receives rainfall. Kerala monsoon starts June 1 (Kerala tourism promotes monsoon travel). "
            "- Rajasthan summer festivals, Rath Yatra in Puri, Valley of Flowers trek in August. "
            "- Northeast India and Western Ghats: Heavy rainfall, some roads close. "
            "- Avoid: Ladakh roads (flash floods), Sundarbans (cyclone risk). "
            "Best festivals by season: "
            "- Diwali (October/November), Dussehra (October), Pushkar Fair (November), "
            "- Holi (March), Pongal (January), Durga Puja (October, Kolkata). "
        ),
        metadata={"source": "india_seasonal_guide.txt", "category": "seasonal_planning", "region": "all_india", "destination": "India"}
    ),

    # ── JAIPUR ACCESSIBILITY ───────────────────────────────────────────────
    Document(
        page_content=(
            "Jaipur, known as the Pink City, is famous for its stunning architecture. "
            "The City Palace and Amer Fort are incredibly popular with tourists. "
            "The best time to visit is during winter (October to February). "
            "Amer Fort has uneven terrain and many steps, making it less suitable for those with mobility issues. "
            "However, the City Palace has paved pathways and is more accessible. "
            "Hawa Mahal is best viewed from outside — the interior has many steep stairs. "
            "Jantar Mantar is mostly paved and manageable for those with mild mobility issues. "
            "Jaipur is a great destination for history lovers, architecture enthusiasts, and those interested in Rajasthani culture. "
            "Shopping: Gem and jewellery stores in Johari Bazaar, block-print textiles in Sanganer. "
            "Budget transport: Auto-rickshaws, city buses, and the Jaipur Metro (limited coverage). "
            "The Pink City is at 431m altitude — no altitude concerns."
        ),
        metadata={"source": "rajasthan_guide.txt", "category": "history_and_accessibility", "region": "rajasthan", "destination": "Jaipur"}
    ),

    # ── ODISHA ─────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Odisha (Orissa) is an underrated treasure with outstanding temples, Tribal India, and pristine beaches. "
            "Konark Sun Temple: UNESCO World Heritage — a 13th-century temple shaped like a colossal chariot of the Sun God. "
            "Puri: One of the four sacred Hindu dhams (Char Dham). Jagannath Temple (one of India's largest, non-Hindus not permitted inside). "
            "Rath Yatra chariot festival (June/July) — one of Asia's largest religious gatherings. "
            "Chilika Lake: Asia's largest brackish water lagoon; Irrawaddy dolphins, migratory flamingoes. "
            "Bhubaneswar: Temple city with 600+ temples; Lingaraja Temple (most famous), Mukteshwar Temple (10th century). "
            "Tribal Odisha: Koraput district — Dongariya Kondh and Bonda tribes; market day visits possible through authorised tour operators. "
            "Puri beach: Long beach, sunrise and sunset views. "
            "Daringbadi (Odisha's Kashmir): Misty pine forest and coffee plantations at 900m. "
            "Bhitarkanika National Park: Second largest mangrove in India; saltwater crocodiles, monitor lizards. "
            "Best time: October to February. Avoid May–August (hot, cyclone risk). "
            "Food: Dalma, Pakhala (fermented rice), Chhena Poda (unique baked cheese dessert)."
        ),
        metadata={"source": "odisha_guide.txt", "category": "state_overview", "region": "east_india", "destination": "Odisha"}
    ),

    # ── ANDHRA PRADESH & TELANGANA ──────────────────────────────────────────
    Document(
        page_content=(
            "Andhra Pradesh and Telangana share a rich cultural heritage of Buddhist sites, forts, and coastline. "
            "Visakhapatnam (Vizag): Coastal city with Ramakrishna Beach, Araku Valley (3 hrs by train through tunnels and ghats), "
            "Borra Caves (limestone, stalagmites), Kailasagiri hill park, Submarine Museum. "
            "Tirupati: Venkateshwara Temple — most visited religious site on earth; 50,000–1,00,000 pilgrims daily. "
            "Special entry darshan tickets (₹300) for shorter queue. Hair tonsuring tradition. Laddu prasadam famous. "
            "Arjuna's Penance Mahabalipuram (shared with Tamil Nadu travel context). "
            "Amaravathi: Ancient Buddhist capital on the Krishna River; Amaravathi Stupa. "
            "Nagarjuna Sagar: Dam and Buddhist island (Nagarjunakonda); boat to reach museum. "
            "Hyderabad (Telangana) — (covered separately). "
            "Warangal (Telangana): Kakatiya Kala Thoranam (stone gateway), Ramappa Temple (UNESCO), 1000-pillar temple. "
            "Best time: October to February. Summers hit 40–45°C in the interior. "
            "Andhra cuisine: Spiciest in India; Gongura (sorrel leaf curry), Pesarattu (green moong dosa), Pulihora (tamarind rice)."
        ),
        metadata={"source": "andhra_telangana_guide.txt", "category": "state_overview", "region": "south_india", "destination": "Andhra Pradesh"}
    ),

    # ── PUNE ────────────────────────────────────────────────────────────────
    Document(
        page_content=(
            "Pune (Poona) is Maharashtra's cultural capital and India's 'Oxford of the East' — a city of education, IT, and history. "
            "Shaniwar Wada: Fortified palace of the Maratha Peshwa rulers; ruined but historically significant. "
            "Aga Khan Palace: Where Mahatma Gandhi was interned; serene museum and memorial. "
            "Sinhagad Fort (24 km): 17th-century fort with a legendary battle story; trekking and viewpoints. "
            "Osho International Meditation Resort: Global centre for meditation; visitors welcome with HIV-negative certificate. "
            "Temples: Dagdusheth Halwai Ganesh Temple, Parvati Hill Temple. "
            "Day trips: Lonavala and Khandala (60 km): Hill stations; Bhushi Dam, Karla and Bhaja Caves (Buddhist rock-cut caves). "
            "Mahabaleshwar (120 km): Hill station with Venna Lake, Elephant Head Point, strawberry farms. "
            "Ajanta (300 km) and Ellora (230 km) Caves — possible day trips from Aurangabad (close to Pune). "
            "Best time: October to February. Monsoon (June–September) transforms Sahyadri Western Ghats into lush green beauty. "
            "Food: Misal Pav, Bhakarwadi, Puran Poli, Modak. FC Road and Camp area for restaurants. "
            "Pune has a vibrant nightlife and cafe culture (Koregaon Park, Boat Club Road)."
        ),
        metadata={"source": "pune_guide.txt", "category": "city_guide", "region": "west_india", "destination": "Pune"}
    ),

    # ── INDIA FAMILY TRAVEL ───────────────────────────────────────────────
    Document(
        page_content=(
            "India with kids — best family-friendly destinations and tips: "
            "Best family destinations: "
            "Goa: Beach holidays, water sports, flat terrain easy for kids and elderly. "
            "Jaipur and Rajasthan: Camel rides, elephant interactions, colourful bazaars; kids love forts and palaces. "
            "Mysore Zoo: One of India's best — kids love the animals. "
            "Ramoji Film City, Hyderabad: Film tours, theme park rides, one of the world's largest studio complexes. "
            "Ranthambore / Corbett Tiger Safaris: Exciting for older children (8+). "
            "Andaman Islands: Snorkelling, sea walks, glass-bottom boats; safe beaches for families. "
            "Coorg (Karnataka): Coffee plantation stays, Dubare Elephant Camp (elephant bathing). "
            "Science City, Kolkata / Nehru Science Centre, Mumbai: Interactive for children. "
            "Tips for families with young children: "
            "- Carry ORS sachets and a basic medical kit. "
            "- Stick to bottled water strictly. "
            "- Book AC train/bus compartments for long journeys. "
            "- Most top hotels have baby cots and children's menus. "
            "- Indian railways have special ladies' and family compartments. "
            "- Major cities have McDonalds, Pizza Hut, and international food chains if children are food-fussy."
        ),
        metadata={"source": "india_family_travel.txt", "category": "family_travel", "region": "all_india", "destination": "India Family"}
    ),

    # ── INDIA SOLO FEMALE TRAVEL ──────────────────────────────────────────
    Document(
        page_content=(
            "Solo female travel in India — safe destinations and practical tips: "
            "Safest cities for solo women: Kochi, Pondicherry, Mysore, Udaipur, Jodhpur, McLeod Ganj, Rishikesh. "
            "Goa is generally safe in popular tourist areas but exercise caution at night on isolated beaches. "
            "Tips: "
            "- Dress conservatively outside tourist areas (salwar kameez or loose trousers and scarves are ideal). "
            "- Use Ola/Uber app-based cabs with driver details tracked. "
            "- Share trips with trusted contacts. "
            "- Book accommodation in well-reviewed properties. Zostel hostels have female-only dorms. "
            "- Avoid isolated places after dark. "
            "- 'Women only' coaches available on Delhi Metro and many city buses. "
            "- In Kerala, Tamil Nadu, and Karnataka, women generally face less harassment. "
            "- Hill stations (Manali, Shimla, Ooty, Munnar) have many solo female travelers and are generally safe. "
            "- Emergency number: 100 (Police), 112 (All emergencies), 1091 (Women's helpline). "
            "- Aasra (mental health/support): 9820466627. "
            "Community: iGoUgo, Tripoto, Women on Wanderlust (Indian solo female traveler groups)."
        ),
        metadata={"source": "india_solo_female_travel.txt", "category": "solo_travel", "region": "all_india", "destination": "India Solo"}
    ),

    # ── LAKSHADWEEP ───────────────────────────────────────────────────────
    Document(
        page_content=(
            "Lakshadweep is India's smallest union territory — a group of 36 coral atolls in the Arabian Sea. "
            "Only 10 of 36 islands are inhabited. Entry requires a Lakshadweep permit (obtainable through Society for Promotion of Nature Tourism and Sports — SPORTS). "
            "Agatti Island: Main entry point with the only airport; beautiful lagoon. "
            "Bangaram Island: Uninhabited resort island; snorkelling, scuba diving, fishing. "
            "Kavaratti: Capital island; Ujra Mosque with dolphin-bone wooden pillars. "
            "Minicoy Island: Southernmost atoll; distinct culture influenced by Maldivian lifestyle; lighthouse. "
            "Marine activities: Snorkelling, scuba diving (world-class coral reefs), glass-bottom boat, kayaking. "
            "No private vehicles; walking and bicycles only on islands. "
            "No alcohol on most islands (Bangaram Resort is an exception). "
            "Best time: October to May. Avoid June–September (monsoon; rough seas; ferries cancelled). "
            "Reaching Lakshadweep: Flight to Agatti from Kochi (1.5 hrs). Ship from Kochi (14–20 hours). "
            "Tourism is limited by the government to protect the fragile coral ecosystem. "
            "Accommodation: Government guesthouses on budget. Bangaram Island Resort for luxury."
        ),
        metadata={"source": "lakshadweep_guide.txt", "category": "beach_diving", "region": "island_india", "destination": "Lakshadweep"}
    ),

    # ── INDIA ROAD TRIPS ──────────────────────────────────────────────────
    Document(
        page_content=(
            "India offers spectacular road trip routes for those who want to explore by road: "
            "Manali to Leh Highway: 490 km, one of the world's highest motorable roads. "
            "Crosses Rohtang Pass, Baralacha La, Tanglang La. Open June–October. Best in July–August. "
            "Stunning landscapes — high-altitude desert, river valleys, glaciers. "
            "Golden Triangle (Delhi–Agra–Jaipur): Classic 750 km circuit; easy on well-maintained National Highway. "
            "Can be done in 5–7 days at leisure. Includes Taj Mahal, Amer Fort, Fatehpur Sikri. "
            "Coastal Karnataka and Goa: NH66 from Mumbai to Goa to Mangalore — lush Western Ghats, sea views, Mahabaleshwar hills. "
            "Northeast Himalayan Circuit (Assam–Meghalaya–Sikkim): Green hills, living root bridges, rhino safaris. Stunning but road conditions variable. "
            "Rajasthan Desert Circuit: Jaipur–Pushkar–Jodhpur–Jaisalmer–Bikaner. 5–7 days. "
            "Spiti Valley Circuit: Shimla–Narkanda–Nako–Tabo–Kaza–Kunzum–Manali. Best July–September. "
            "Andaman islands road trips: AH1 highway connects Port Blair to Diglipur — scenic coastal drive. "
            "Practical notes: Hire a self-drive car or book a driver for long-distance. Carry extra fuel in remote areas. "
            "Download offline maps (Google Maps, Maps.me). Road conditions in mountains can be challenging."
        ),
        metadata={"source": "india_road_trips.txt", "category": "road_trips", "region": "all_india", "destination": "India Road Trips"}
    ),

    # ── INDIA HILL STATIONS ────────────────────────────────────────────────
    Document(
        page_content=(
            "India's best hill stations span the Himalayas, Western Ghats, and Eastern Ghats: "
            "Shimla (HP, 2,205m): Former British summer capital; Ridge, Mall Road, Jakhu Temple (monkeys!), toy train. Best April–June, Sept–Oct. "
            "Manali (HP, 2,050m): Adventure hub; Rohtang Pass, Solang Valley, Hadimba Temple. Open May–Oct for road trips. "
            "Mussoorie (UK, 2,005m): Queen of Hills; Kempty Falls, Gun Hill, Landour heritage neighbourhood. "
            "Nainital (UK, 2,084m): Lake district; Naini Lake boating, Snow View by cable car. "
            "Darjeeling (WB, 2,042m): Toy train, tea gardens, Himalayan views. Best March–June, Sept–Nov. "
            "Shillong (Meghalaya, 1,491m): Scotland of the East; Ward's Lake, Elephant Falls, Don Bosco Museum. "
            "Ooty (TN, 2,240m): Tea estates, botanical garden, Nilgiri toy train. Best April–June. "
            "Coorg / Madikeri (Karnataka, 1,525m): Coffee estates, misty forests, Abbey Falls, Raja's Seat. Best Oct–March. "
            "Munnar (Kerala, 1,600m): Endless tea estates, Eravikulam National Park, Top Station. Best Sept–May. "
            "Kodaikanal (TN, 2,133m): Pillar Rocks, Silver Cascade, Kodai Lake. Best April–June. "
            "Panchgani and Mahabaleshwar (Maharashtra): Strawberry farms, tablelands, boating. Best Oct–June. "
            "Tips: Book accommodation in advance for peak season. Hill station roads can be narrow and winding."
        ),
        metadata={"source": "india_hill_stations_guide.txt", "category": "hill_stations", "region": "all_india", "destination": "India Hill Stations"}
    ),

    # ── INDIA MUSEUMS ─────────────────────────────────────────────────────
    Document(
        page_content=(
            "India's best museums offer deep dives into art, history, science, and culture: "
            "National Museum, New Delhi: One of Asia's largest; 2 lakh objects spanning 5,000 years. Indus Valley Civilization, Mughal art, tribal artefacts. "
            "Indian Museum, Kolkata: Asia's oldest museum (1814); natural history, archaeology, art. "
            "Chhatrapati Shivaji Maharaj Vastu Sangrahalaya (Prince of Wales Museum), Mumbai: Magnificent Indo-Saracenic building; art and natural history. "
            "Salar Jung Museum, Hyderabad: One of India's largest; vast eclectic collection from around the world. "
            "Government Museum, Chennai: Second oldest museum in India; natural history, bronze Chola sculptures. "
            "National Rail Museum, New Delhi: Open-air museum with vintage steam engines; great for families and children. "
            "Craft Museum (National Crafts Museum), New Delhi: Living crafts from all states; artisans demonstrate live. "
            "Victoria Memorial, Kolkata: Stunning colonial museum; British India history, paintings, sculptures. "
            "Archaeological Survey of India (ASI) Site Museums: Small but excellent museums at major archaeological sites (Nalanda, Sarnath, Sanchi). "
            "Partition Museum, Amritsar: Moving documentation of 1947; highly recommended. "
            "Most national museums are open Tuesday–Sunday (closed Mondays). Entry fees: ₹20–100 for Indians."
        ),
        metadata={"source": "india_museums_guide.txt", "category": "museums_culture", "region": "all_india", "destination": "India Museums"}
    ),

    # ── INDIA CULTURAL EXPERIENCES ────────────────────────────────────────
    Document(
        page_content=(
            "Unique cultural experiences to seek in India: "
            "Festivals: Holi (March — Mathura/Vrindavan for the most authentic experience), Diwali (October/November — Varanasi for River Ganga fireworks), "
            "Pushkar Camel Fair (November — extraordinary), Hornbill Festival Nagaland (December), "
            "Thrissur Pooram Kerala (April/May — temple elephant procession), Hemis Festival Ladakh (June). "
            "Classical music and dance: Carnatic music concerts in Chennai (December–January Music Season), "
            "Bharatanatyam at Brihadeeswarar Temple (Thanjavur), Kathakali performances in Kochi, "
            "Odissi dance in Bhubaneswar, Manipuri dance in Imphal. "
            "Craft workshops: Block printing in Sanganer (Jaipur), pottery in Khurja (UP), "
            "Pattachitra painting in Raghurajpur (Odisha), Madhubani painting in Bihar. "
            "Cooking classes: Kerala Ayurvedic cooking, Rajasthani dal baati cooking in heritage homes, "
            "Bengali sweet-making in Kolkata, home-style Gujarati thali preparation. "
            "Yoga and meditation retreats: Rishikesh (global yoga capital), Mysore (Ashtanga yoga with Sharath Jois), "
            "Osho Meditation Resort Pune, Vipassana centres across India (10-day free silent retreats). "
            "Tribal experiences: Gondi culture (Chhattisgarh), Kutchi craft villages (Gujarat), "
            "Naga tribal villages Nagaland (Hornbill Festival only), Bishnoi villages Jodhpur (eco-friendly community)."
        ),
        metadata={"source": "india_cultural_experiences.txt", "category": "cultural_experiences", "region": "all_india", "destination": "India Culture"}
    ),
]


class DocumentIngestor:
    def __init__(self, persist_directory="data/vector_db"):
        self.persist_directory = persist_directory
        # Use a lightweight embedding model
        self.embeddings = HuggingFaceEmbeddings(model_name="all-MiniLM-L6-v2")

        # Ensure the directory exists
        os.makedirs(self.persist_directory, exist_ok=True)

        # Initialize ChromaDB
        self.vector_db = Chroma(
            collection_name="travel_knowledge",
            embedding_function=self.embeddings,
            persist_directory=self.persist_directory
        )

    def load_india_knowledge_base(self):
        """
        Load the comprehensive India knowledge base into ChromaDB.
        This replaces any previously loaded mock data with rich, factual content
        covering all major Indian states, cities, attractions, and travel tips.
        """
        print(f"Ingesting comprehensive India knowledge base ({len(INDIA_KNOWLEDGE_BASE)} documents) into ChromaDB...")

        # Clear existing collection to avoid duplicates on re-ingestion
        try:
            existing = self.vector_db.get()
            existing_count = len(existing.get("ids", []))
            if existing_count > 0:
                print(f"  Clearing {existing_count} existing documents before re-ingestion...")
                self.vector_db.delete(ids=existing["ids"])
        except Exception as e:
            print(f"  [WARN] Could not clear existing documents: {e}")

        # Add documents in batches for efficiency
        batch_size = 20
        total = len(INDIA_KNOWLEDGE_BASE)
        for i in range(0, total, batch_size):
            batch = INDIA_KNOWLEDGE_BASE[i:i + batch_size]
            self.vector_db.add_documents(batch)
            print(f"  Ingested documents {i+1}–{min(i+batch_size, total)} of {total}")

        self.vector_db.persist()
        print(f"[SUCCESS] Ingested {total} India knowledge documents into {self.persist_directory}")

    # Keep backward-compatible alias
    def load_mock_data(self):
        """Backward-compatible alias for load_india_knowledge_base."""
        return self.load_india_knowledge_base()

    def query(self, query_text, k=2):
        """Query the vector database."""
        results = self.vector_db.similarity_search(query_text, k=k)
        return results

    def get_document_count(self):
        """Return total number of documents in the vector store."""
        try:
            data = self.vector_db.get()
            return len(data.get("ids", []))
        except Exception:
            return 0


if __name__ == "__main__":
    ingestor = DocumentIngestor()

    if "--rebuild" in sys.argv or len(ingestor.vector_db.get().get("ids", [])) == 0:
        print("Building India knowledge base...")
        ingestor.load_india_knowledge_base()
    else:
        print(f"India knowledge base already exists ({ingestor.get_document_count()} documents). Use --rebuild to re-ingest.")

    # Test queries
    test_queries = [
        "vegetarian food in goa",
        "wheelchair accessibility in Jaipur",
        "best time to visit Kerala",
        "tiger safari in Rajasthan",
        "beaches in India",
        "history and heritage sites in Mumbai",
        "trekking in Himalayas",
        "budget travel in Varanasi",
    ]

    print("\n" + "="*60)
    print("TESTING INDIA KNOWLEDGE BASE QUERIES")
    print("="*60)
    for q in test_queries:
        print(f"\nQuery: '{q}'")
        results = ingestor.query(q, k=2)
        for r in results:
            dest = r.metadata.get("destination", "Unknown")
            cat = r.metadata.get("category", "Unknown")
            print(f"  → [{dest} | {cat}] {r.page_content[:120]}...")
