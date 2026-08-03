import json
log_path = r'C:\Users\PHANINDHRA\.gemini\antigravity-ide\brain\977859fa-4510-4721-8092-a526fe09000b\.system_generated\logs\transcript.jsonl'
user_msgs = []
with open(log_path, encoding='utf-8') as f:
    for line in f:
        data = json.loads(line)
        if data.get('type') == 'USER_INPUT':
            user_msgs.append(data.get('content', ''))
with open(r'd:\project\explorex_main_india\user_msgs.json', 'w', encoding='utf-8') as f:
    json.dump(user_msgs, f, indent=2)
