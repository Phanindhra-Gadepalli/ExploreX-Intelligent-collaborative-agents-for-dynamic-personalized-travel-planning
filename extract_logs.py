import json
import traceback

log_path = r'C:\Users\PHANINDHRA\.gemini\antigravity-ide\brain\977859fa-4510-4721-8092-a526fe09000b\.system_generated\logs\transcript.jsonl'
output_path = r'd:\project\explorex_main_india\extracted_user_msgs.txt'

try:
    with open(log_path, 'r', encoding='utf-8') as f_in, open(output_path, 'w', encoding='utf-8') as f_out:
        for i, line in enumerate(f_in):
            try:
                data = json.loads(line)
                if data.get('type') == 'USER_INPUT':
                    f_out.write(f"=== Msg {i} ===\n{data.get('content', '')}\n\n")
            except Exception as e:
                pass
except Exception as e:
    with open(output_path, 'w', encoding='utf-8') as f_out:
        f_out.write(str(e) + "\n" + traceback.format_exc())
