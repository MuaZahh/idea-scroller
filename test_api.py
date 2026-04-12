"""One-off test: read key from .env and call Claude."""
import anthropic

key = ""
for line in open(".env").read().splitlines():
    if line.startswith("ANTHROPIC_API_KEY="):
        key = line.split("=", 1)[1].strip().strip('"').strip("'").strip()
        break

print(f"Key: [{key[:15]}...{key[-5:]}] len={len(key)}")

client = anthropic.Anthropic(api_key=key)
resp = client.messages.create(
    model="claude-sonnet-4-6",
    max_tokens=10,
    messages=[{"role": "user", "content": "Say hi"}],
)
print(f"OK: {resp.content[0].text}")
