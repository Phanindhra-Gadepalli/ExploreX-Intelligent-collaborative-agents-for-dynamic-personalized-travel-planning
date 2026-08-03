import os
from typing import Optional, Dict, Any, Union
import json
import re
import dotenv

dotenv.load_dotenv()

# Use Google Gemini to ask questions
def ask_openai(
    prompt: str,
    model: str = "gemini-flash-lite-latest",
    temperature: float = 0.7,
    max_tokens: int = 600  # Reduced for India-only scope — faster & cheaper LLM calls
) -> Optional[Dict[str, Any]]:
    """Wrapper kept as ask_openai for compatibility, now uses Gemini under the hood."""
    try:
        import google.generativeai as genai
        genai.configure(api_key=os.getenv("GEMINI_API_KEY"))
        gemini_model = genai.GenerativeModel(model)
        response = gemini_model.generate_content(
            prompt,
            generation_config=genai.GenerationConfig(
                temperature=temperature,
                max_output_tokens=max_tokens
            )
        )
        answer = response.text
        return {"answer": answer}
    except Exception as e:
        print(f"❌ fail in ask_openai (Gemini): {str(e)}")
        return None


def extract_number(text: str) -> Optional[float]:
    """
    Extract numbers from text, supporting multiple formats
    
    Args:
        text: Text containing numbers
    
    Returns:
        Extracted number (float), returns None if not found
    
    Examples:
        >>> extract_number("Price is $3.45 per gallon")
        3.45
        >>> extract_number("Gas price 4.25 dollars")
        4.25
        >>> extract_number("3,456.78")
        3456.78
    """
    try:
        # Remove all whitespace characters
        text = text.strip()
        
        # Try direct conversion to float
        try:
            return float(text)
        except ValueError:
            pass
        
        # Remove currency symbols and other non-numeric characters (keep numbers, decimal points and commas)
        text = re.sub(r'[^\d.,]', '', text)
        
        # Handle different decimal point formats
        if ',' in text and '.' in text:
            # If both comma and decimal point exist, assume comma is thousand separator
            text = text.replace(',', '')
        elif ',' in text:
            # If only comma exists, assume it's decimal point
            text = text.replace(',', '.')
        
        # Extract first number
        match = re.search(r'\d+\.?\d*', text)
        if match:
            return float(match.group())
            
        return None
        
    except Exception:
        return None

def extract_price(text: str, currency: str = "USD") -> Optional[float]:
    """
    Extract price from text, supporting multiple formats
    
    Args:
        text: Text containing price
        currency: Currency type (default USD)
    
    Returns:
        Extracted price (float), returns None if not found
    """
    try:
        text = text.strip()
        text = text.replace(currency, '').replace('$', '').replace('¥', '').replace('€', '')
        return extract_number(text)
    except Exception:
        return None
