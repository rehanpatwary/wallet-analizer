import json

s = json.load(open('tx_fetch_state.json'))
print("Type of top level:", type(s))
if isinstance(s, dict):
    print("Keys sample:", list(s.keys())[:3])
    first_val = list(s.values())[0]
    print("Type of first value:", type(first_val))
    print("First value:", first_val)
else:
    print("Length:", len(s))
    print("First item:", s[0] if s else None)
