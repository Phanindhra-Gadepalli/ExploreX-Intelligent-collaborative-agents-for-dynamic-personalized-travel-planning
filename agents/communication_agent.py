from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_core.messages import SystemMessage, HumanMessage

class CommunicationAgent:
    def __init__(self, model_name="gemini-flash-lite-latest"):
        """Initialize CommunicationAgent with AI model for communication."""
        self.model = ChatGoogleGenerativeAI(model=model_name, temperature=0.7)
    
    def post_car_rental_request(self, location, duration, user_prefs):
        """Generate car rental request post."""
        # Convert duration to integer if it's a string
        duration = int(duration) if isinstance(duration, str) else duration
        
        # Get user preferences with defaults and proper type conversion
        num_people = int(user_prefs.get('people', 1)) if isinstance(user_prefs.get('people'), str) else user_prefs.get('people', 1)
        has_kids = user_prefs.get('kids', False)
        if isinstance(has_kids, str):
            has_kids = has_kids.lower() == 'yes' or has_kids.lower() == 'true'
        budget_level = user_prefs.get('budget', 'medium')
        
        # Format the prompt with consistent information
        prompt = f"""
        Generate a car rental request post for the following trip:
        
        Location: {location}
        Duration: {duration} days
        Number of people: {num_people}
        Kids: {'Yes' if has_kids else 'No'}
        Budget level: {budget_level}
        
        The post should be polite, clear, and include all necessary information.
        {f'Include a request for child seats if available.' if has_kids else ''}
        Make sure the information about kids and budget matches exactly with the provided details.
        """
        
        messages = [
            SystemMessage(content="You are a helpful assistant creating a car rental request post. Ensure all information is accurate and matches the provided details exactly."),
            HumanMessage(content=prompt)
        ]
        
        response = self.model.invoke(messages)
        
        post_content = response.content
        if isinstance(post_content, list):
            post_content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in post_content)
            
        # Return structured data with consistent information
        return {
            "post_content": post_content,
            "location": location,
            "duration": duration,
            "status": "pending",
            "user_prefs": {
                "num_people": num_people,
                "has_kids": has_kids,
                "budget_level": budget_level
            }
        }
    
    def handle_rental_response(self, rental_post, response_message):
        """Handle response to car rental request."""
        prompt = f"""
        A car rental company has responded to the following car rental request:
        
        Original request:
        {rental_post['post_content']}
        
        Their response:
        {response_message}
        
        Please draft a polite reply that:
        1. Thanks them for their response
        2. Asks any necessary follow-up questions about pricing, car type, pickup details, etc.
        3. Is friendly and professional
        """
        
        messages = [
            SystemMessage(content="You are a helpful assistant handling communications about car rentals."),
            HumanMessage(content=prompt)
        ]
        
        response = self.model.invoke(messages)
        
        reply_content = response.content
        if isinstance(reply_content, list):
            reply_content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in reply_content)
        
        return {
            "reply_content": reply_content,
            "original_post": rental_post,
            "response_message": response_message
        }
    
    def generate_booking_confirmation(self, itinerary, budget_estimate, car_rental=None, user_name=None):
        """Generate booking confirmation message."""
        itinerary_summary = f"{len(itinerary)} days, starting on {itinerary[0]['date'] if itinerary else 'N/A'}"
        attractions_count = sum(len(day['spots']) for day in itinerary) if itinerary else 0
        name = user_name if user_name else "Traveler"
        
        # Only include car rental information if it's recommended
        car_rental_prompt = f"\nCar rental: {'Yes' if car_rental else 'No'}" if car_rental else ""
        
        prompt = f"""
        Generate a friendly, comprehensive trip confirmation message to {name} with the following details:
        
        Itinerary: {itinerary_summary}
        Number of attractions: {attractions_count}
        Estimated budget: ${budget_estimate['total']}{car_rental_prompt}
        
        The message should:
        1. Confirm the booking is complete
        2. Summarize the trip details
        3. Mention that a detailed itinerary is attached
        4. Provide any useful tips for preparation
        5. Be friendly and excited about their upcoming trip
        """
        
        messages = [
            SystemMessage(content="You are a travel assistant called ExploreX sending a trip confirmation message. Pay attention to the email format."),
            HumanMessage(content=prompt)
        ]
        
        response = self.model.invoke(messages)
        
        content = response.content
        if isinstance(content, list):
            content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
        
        return content

    def generate_travel_tips_and_summary(self, user_prefs, selected_attractions, budget_estimate, transit_options):
        """Generate a combined summary including local travel tips, recommendation summary, budget, and transit."""
        city = user_prefs.get('city', 'the destination')
        days = user_prefs.get('days', 1)
        name = user_prefs.get('name', 'Traveler')
        
        attractions_list = [a.get("name") for a in selected_attractions]
        budget_total = budget_estimate.get('total', 0) if budget_estimate else 0
        
        has_transit = transit_options and any(transit_options.values())
        
        prompt = f"""
        You are the Communication Agent for ExploreX. {name} has selected their attractions for a {days}-day trip to {city}.
        Before we generate the final route itinerary, you must provide a comprehensive pre-itinerary summary.
        
        Selected Attractions: {', '.join(attractions_list)}
        Estimated Budget: ₹{budget_total}
        Transit Options Available: {'Yes' if has_transit else 'No'}
        
        Please generate a beautifully formatted Markdown response containing:
        1. **Recommendation Summary**: A brief, exciting summary of the selected attractions and overall trip vibe.
        2. **Budget Summary**: A high-level overview of the estimated budget (mentioning it's around ₹{budget_total}).
        3. **Transit Summary**: A brief mention of travel options to {city}.
        4. **Local Travel Tips & Advice**: 3-4 important tips specific to {city} (e.g., local customs, weather advice, safety, local transport like autos/metros).
        
        End the message by asking the user to explicitly confirm they are ready to build the final itinerary (e.g., "Are you ready to proceed with generating the final itinerary?").
        """
        
        messages = [
            SystemMessage(content="You are ExploreX's Communication Agent. Provide helpful summaries and travel tips in Markdown format."),
            HumanMessage(content=prompt)
        ]
        
        try:
            response = self.model.invoke(messages)
            content = response.content
            if isinstance(content, list):
                content = "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content)
            return content
        except Exception as e:
            print(f"[ERROR] CommunicationAgent failed to generate tips: {e}")
            return "Here is a summary of your trip. Are you ready to build the final itinerary?"