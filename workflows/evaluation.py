import os
import openai
import re
from dotenv import load_dotenv
load_dotenv()
def evaluate_state_with_llm(state: dict):
    """
    use LLM to evaluate the self.state dictionary content.
    """
   

    prompt = (
        f"""
The following is the user's travel need (given in Python dictionary format):
user_info: {state['user_info']}
selected_attractions: {state['selected_attractions']}

The following is the generated travel plan result:
ai_recommendation_generated: {state['ai_recommendation_generated']}
itinerary: {state['itinerary']}
budget: {state['budget']}
rental_post: {state['rental_post']}
should_rent_car: {state['should_rent_car']}

Please evaluate whether the generated travel plan meets the user's needs and preferences.
Give a score (1-10) for user satisfaction, and provide a brief comment on why.
Please strictly output in the following format:
score: <score>
comment: <your comment>
"""
    )

    client = openai.OpenAI(api_key=os.getenv("OPENAI_API_KEY"))
    response = client.chat.completions.create(
        model="gpt-3.5-turbo",
        messages=[
            {"role": "system", "content": "You are a user."},
            {"role": "user", "content": prompt}
        ],
        temperature=0.7
    )
    llm_output = response.choices[0].message.content
    print("LLM原始输出：")
    print(llm_output)

    save_score_and_comment(state,llm_output)

def save_score_and_comment(state,llm_output, filename="evaluation score.txt"):
    # extract score and comment from llm_output
    score_match = re.search(r"score[:：]\s*([0-9]{1,2})", llm_output, re.IGNORECASE)
    comment_match = re.search(r"comment[:：]\s*(.*)", llm_output, re.IGNORECASE | re.DOTALL)

    score = score_match.group(1) if score_match else "N/A"
    comment = comment_match.group(1).strip() if comment_match else "N/A"

    # save to file
    with open(filename, "w", encoding="utf-8") as f:
        f.write(f"output: {state}\n")
        f.write(f"score: {score}\n")
        f.write(f"comment: {comment}\n")

    print(f"finish evaluation and save to {filename}")


## 后续需要添加的
# 1. 改完recommend_agent,prompt 添加additional_attractions
# 2. 加入few-shot prompt, 帮助生成comment

