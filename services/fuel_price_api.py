import json
import os
from typing import Optional, Dict, Any
from utils import ask_openai, extract_price
import time
## 提供每个国家最近的油价（API太贵）
def get_gas_price(city: str) -> Optional[float]:
    """
    get the gas price of a city
    
    Args:
        city: city name

    Returns:
        gas price in USD PER GALLON, if not found, return None
    """
    try:
        data_path = os.path.join('data', 'global_fuel_prices.json')
        with open(data_path, 'r', encoding='utf-8') as f:
            price_data = json.load(f)
        
        prompt = f"please determine the country of {city}/n"
        prompt += "just return the country name, no other words"
        country_response = ask_openai(prompt)
    
        country = country_response["answer"].strip()
        print(f"sucessfully get the country of {city}: {country}")
        
        if country in price_data:
            return price_data[country]
        
        else:
            print(f" haven't found the price of {city} in the data, try to get it from openai")
            max_attempts = 3  
            for attempt in range(max_attempts):
                price_response = ask_openai(
                    f"please give the gas price in USD PER GALLON of {city},"
                    f"just return the number, don't include any other text."
                )
                
                if not price_response:
                    continue
                    
                price = extract_price(price_response["answer"])
                if price is not None:
                    return price
                    
                if attempt < max_attempts - 1:
                    time.sleep(1)
                    continue
            
            print(f"❌ can't get the price of {city} after {max_attempts} attempts")
            return None
        
    except FileNotFoundError:
        print(f"❌ can't find the price data file")
        return None
    except Exception as e:
        print(f"❌ error when processing the data: {str(e)}")
        return None


