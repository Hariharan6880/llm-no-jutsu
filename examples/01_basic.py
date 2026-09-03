"""The smallest thing that works: prompt in, text out."""

from devllm import ClaudeCLI

llm = ClaudeCLI(model="sonnet")

response = llm.generate("Name three phone brands sold in India. One sentence.")

print(response.text)
print(f"\n[{response.backend}/{response.model} in {response.duration_s:.1f}s]")
if response.usage:
    print(f"[tokens in={response.usage.total_input} "
          f"out={response.usage.output_tokens}]")
