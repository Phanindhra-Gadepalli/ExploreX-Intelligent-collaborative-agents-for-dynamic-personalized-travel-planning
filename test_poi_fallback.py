import pytest
import os
import googlemaps
from unittest.mock import MagicMock, patch
from services.poi_fallback import POIManager
from agents.information_agent import InformationAgent
from agents.recommend_agent import RecommendAgent

@pytest.fixture
def mock_poi_manager():
    manager = POIManager()
    # Give a dummy geoapify key to enable the Geoapify fallback in tests
    manager.geoapify_key = "dummy_geoapify_key" 
    manager.maps_api_key = "dummy_maps_key"
    return manager

@patch('services.poi_fallback.requests.post')
@patch('services.poi_fallback.requests.get')
def test_fallback_cascade_google_fails(mock_get, mock_post, mock_poi_manager):
    """Test that Geoapify is called as the first primary."""
    # 0. Mock OSM Overpass to fail
    mock_post.side_effect = Exception("OSM Failed")

    # 1. Mock Geoapify to succeed
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "features": [
            {
                "properties": {
                    "place_id": "geo1",
                    "name": "Kashi Vishwanath Temple",
                    "lat": 25.3109,
                    "lon": 83.0076,
                    "categories": ["tourism.attraction"]
                }
            }
        ]
    }
    mock_get.return_value = mock_resp
    
    # Call the manager
    results = mock_poi_manager.get_attractions(lat=25.31, lng=83.01)
    
    # Assertions
    assert len(results) == 1
    assert results[0]['name'] == "Kashi Vishwanath Temple"
    assert results[0]['source'] == "geoapify"
    assert mock_get.called
    assert mock_post.called

@patch('services.poi_fallback.requests.post')
@patch('services.poi_fallback.requests.get')
def test_fallback_cascade_geoapify_fails(mock_get, mock_post, mock_poi_manager):
    """Test that OSM Overpass is called if Geoapify fails."""
    # 1. Mock Geoapify to fail
    mock_get.side_effect = Exception("Geoapify Timeout")
    
    # 2. Mock OSM Overpass to succeed
    mock_resp = MagicMock()
    mock_resp.json.return_value = {
        "elements": [
            {
                "id": 12345,
                "lat": 25.31,
                "lon": 83.01,
                "tags": {
                    "name": "Sarnath Stupa",
                    "tourism": "attraction"
                }
            }
        ]
    }
    mock_post.return_value = mock_resp
    
    # Call the manager
    results = mock_poi_manager.get_attractions(lat=25.31, lng=83.01)
    
    # Assertions
    assert len(results) == 1
    assert results[0]['name'] == "Sarnath Stupa"
    assert results[0]['source'] == "osm"
    assert mock_post.called

@patch('agents.recommend_agent.ChatGoogleGenerativeAI')
def test_rag_poi_extraction(mock_llm):
    """Test extracting structured POIs from text using RecommendAgent."""
    agent = RecommendAgent()
    
    # Mock LLM response
    mock_response = MagicMock()
    mock_response.content = '''
    ```json
    [
        {
            "id": "rag_dashashwamedh",
            "name": "Dashashwamedh Ghat",
            "rating": 4.8,
            "user_ratings_total": 500,
            "price_level": 1,
            "address": "Varanasi, India",
            "location": {"lat": 25.3, "lng": 83.0},
            "category": "tourist_attraction",
            "types": ["tourist_attraction"],
            "description": "The main ghat in Varanasi on the Ganga River.",
            "source": "rag_extraction"
        }
    ]
    ```
    '''
    agent.model.invoke.return_value = mock_response
    
    text = "Varanasi is famous for Dashashwamedh Ghat, a bustling area by the Ganges."
    pois = agent.extract_pois_from_text(text, "Varanasi", 25.3, 83.0)
    
    assert len(pois) == 1
    assert pois[0]['name'] == "Dashashwamedh Ghat"
    assert pois[0]['source'] == "rag_extraction"
