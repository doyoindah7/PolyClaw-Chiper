content = open('src/polyclaw_cipher_v3/bot.py', encoding='utf-8').read()
count = content.count('def _auto_tune_from_history')
print(f'Methods: {count}')
