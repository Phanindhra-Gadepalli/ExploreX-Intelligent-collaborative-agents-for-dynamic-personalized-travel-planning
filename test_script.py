import os
from dotenv import load_dotenv

# Ensure telemetry is disabled for the test too
os.environ["ANONYMIZED_TELEMETRY"] = "False"
load_dotenv()

from workflows.travel_graph import TravelGraph

def main():
    print("Initializing TravelGraph...")
    graph = TravelGraph()
    print("TravelGraph initialized successfully.")

    query = "I am Nithin from Kerala. Give me an itinerary for a religious and culture trip to Varanasi for me and my wife for 4 days"
    print(f"Sending query: {query}")

    # Process step
    result = graph.process_step('chat', user_input=query)
    
    # Check if stream is present (usually it is for chat)
    if 'stream' in result:
        print("Received stream. Collecting response...")
        stream_gen = result.pop('stream')
        collected = ""
        for chunk in stream_gen:
            c = chunk.content if hasattr(chunk, 'content') else chunk
            if isinstance(c, list):
                for part in c:
                    if isinstance(part, dict) and 'text' in part:
                        collected += part['text']
            elif isinstance(c, str):
                collected += c
        print("\n=== FINAL RESPONSE ===")
        print(collected)
    else:
        print("\n=== FINAL RESPONSE ===")
        print(result.get('response', 'NO RESPONSE FIELD'))

if __name__ == "__main__":
    main()
