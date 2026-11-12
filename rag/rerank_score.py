"""
Qwen3-Reranker scores for prepared (query, passage) pairs: the GPU-server half of
rag/reranking.py. It needs only vllm or transformers (nothing from this repository), so
the job folder made by `python rag/reranking.py package` runs on its own.

Scoring is SAGEO's QwenReranker (third_party/SAGEO_Arena/src/modules/reranker.py): the
same chat messages, truncation to 10,000 tokens, yes/no suffix, and
score = p(yes) / (p(yes) + p(no)). The model files are pinned to their 2025-06-09
revision, the one SAGEO ran with: the 2026-04-16 commit added chat_template.jinja (roles
"query" / "document"), which recent transformers prefers, and with it SAGEO's system/user
messages render with an empty query and document. Every prompt is checked to contain its
query and passage.
    vllm  SAGEO's settings: generate one token, logprobs 20, prefix caching (pairs come
          grouped by query and document, so a document's metadata prefix is reused)
    hf    transformers forward pass, the two logits at the last position; the same score
          except where SAGEO falls back to logprob -10 for a token outside the top 20
    auto  vllm if it loads and scores a test pair, else hf

Input   <in_dir>/<part>.jsonl.gz   {"key", "query", "passage"}
Output  <out_dir>/<part>.jsonl     {"key", "score"}; a rerun skips keys already scored
        <out_dir>/progress.log     pairs/s, tokens/s and ETA after every chunk

Usage:
    python rerank_score.py --in_dir parts --out_dir out --parts mini trainval val_deep test
    python rerank_score.py ... --model Qwen/Qwen3-Reranker-0.6B --backend hf --limit 200
"""
import argparse
import gzip
import json
import math
import time
from pathlib import Path

MODEL = "Qwen/Qwen3-Reranker-4B"
REVISIONS = {"Qwen/Qwen3-Reranker-4B": "f16fc5d5d2b9b1d0db8280929242745d79794ef5",
             "Qwen/Qwen3-Reranker-0.6B": "6e9e69830b95c52b5fd889b7690dda3329508de3"}  # 2025-06-09
SYSTEM = ('Judge whether the Document meets the requirements based on the Query and the Instruct provided. '
          'Note that the answer can only be "yes" or "no".')
INSTRUCTION = "Given a web search query, retrieve relevant passages that answer the query"
SUFFIX = "<|im_end|>\n<|im_start|>assistant\n<think>\n\n</think>\n\n"
MAX_LEN = 10000

LOG = None


def log(msg: str):
    line = time.strftime("%H:%M:%S ") + msg
    print(line, flush=True)
    if LOG:
        with open(LOG, "a") as f:
            f.write(line + "\n")


class Prompts:
    """Token ids of SAGEO's reranker prompt (QwenReranker._process_inputs)."""

    def __init__(self, tokenizer):
        self.tok = tokenizer
        self.suffix = tokenizer.encode(SUFFIX, add_special_tokens=False)
        self.yes = tokenizer("yes", add_special_tokens=False).input_ids[0]
        self.no = tokenizer("no", add_special_tokens=False).input_ids[0]
        self.keep = MAX_LEN - 1 - len(self.suffix)

    def __call__(self, rows) -> list[list[int]]:
        convs = [[{"role": "system", "content": SYSTEM},
                  {"role": "user", "content": f"<Instruct>: {INSTRUCTION}\n\n<Query>: {r['query']}\n\n<Document>: {r['passage']}"}]
                 for r in rows]
        texts = self.tok.apply_chat_template(convs, tokenize=False, add_generation_prompt=False, enable_thinking=False)
        for r, text in zip(rows, texts):
            if f"<Query>: {r['query']}\n\n<Document>: {r['passage'][:200]}" not in text:
                raise RuntimeError(f"the chat template dropped the query or passage: {text[:400]!r}")
        ids = self.tok(texts, add_special_tokens=False)["input_ids"]
        return [t[:self.keep] + self.suffix for t in ids]


def vllm_scorer(model: str, prompts: Prompts):
    from vllm import LLM, SamplingParams
    try:
        from vllm.inputs import TokensPrompt
    except ImportError:
        from vllm.inputs.data import TokensPrompt
    llm = LLM(model=model, revision=REVISIONS.get(model), tokenizer_revision=REVISIONS.get(model), seed=0,
              gpu_memory_utilization=0.9, max_model_len=MAX_LEN, enable_prefix_caching=True,
              enforce_eager=True, disable_log_stats=True)
    params = SamplingParams(temperature=0, max_tokens=1, logprobs=20, allowed_token_ids=[prompts.yes, prompts.no])

    def score(rows):
        ids = prompts(rows)
        outs = llm.generate([TokensPrompt(prompt_token_ids=t) for t in ids], params, use_tqdm=False)
        scores = []
        for o in outs:
            last = o.outputs[0].logprobs[-1]
            y = last[prompts.yes].logprob if prompts.yes in last else -10
            n = last[prompts.no].logprob if prompts.no in last else -10
            scores.append(math.exp(y) / (math.exp(y) + math.exp(n)))
        return scores, sum(map(len, ids))
    return score


def hf_scorer(model: str, prompts: Prompts, batch_tokens: int = 32768):
    import torch
    from transformers import AutoModelForCausalLM
    net = AutoModelForCausalLM.from_pretrained(model, revision=REVISIONS.get(model), dtype=torch.bfloat16).cuda().eval()
    pad = prompts.tok.pad_token_id if prompts.tok.pad_token_id is not None else prompts.tok.eos_token_id

    @torch.no_grad()
    def score(rows):
        ids = prompts(rows)
        order = sorted(range(len(ids)), key=lambda i: len(ids[i]))
        scores = [0.0] * len(ids)
        i = 0
        while i < len(order):
            j = i + 1  # largest batch whose padded size stays under batch_tokens
            while j < len(order) and len(ids[order[j]]) * (j - i + 1) <= batch_tokens:
                j += 1
            batch = [ids[k] for k in order[i:j]]
            width = len(batch[-1])
            x = torch.full((len(batch), width), pad, dtype=torch.long)
            mask = torch.zeros((len(batch), width), dtype=torch.long)
            for b, t in enumerate(batch):  # left padding: the last position is every prompt's end
                x[b, width - len(t):] = torch.tensor(t)
                mask[b, width - len(t):] = 1
            logits = net(input_ids=x.cuda(), attention_mask=mask.cuda(), logits_to_keep=1).logits[:, -1, :]
            p_yes = torch.stack([logits[:, prompts.no], logits[:, prompts.yes]], 1).float().log_softmax(1)[:, 1].exp()
            for b, k in enumerate(order[i:j]):
                scores[k] = float(p_yes[b])
            i = j
        return scores, sum(map(len, ids))
    return score


def make_scorer(backend: str, model: str, prompts: Prompts, batch_tokens: int):
    if backend in ("vllm", "auto"):
        try:
            score = vllm_scorer(model, prompts)
            score([{"query": "test", "passage": "test"}])
            log("backend: vllm")
            return score
        except Exception as e:
            if backend == "vllm":
                raise
            log(f"vllm unavailable ({type(e).__name__}: {str(e)[:300]}), using transformers")
    log("backend: hf")
    return hf_scorer(model, prompts, batch_tokens)


def read_part(path: Path):
    with gzip.open(path, "rt", encoding="utf-8") as f:
        for line in f:
            yield json.loads(line)


def main():
    global LOG
    parser = argparse.ArgumentParser()
    parser.add_argument("--in_dir", required=True)
    parser.add_argument("--out_dir", required=True)
    parser.add_argument("--parts", nargs="+", default=["mini", "trainval", "val_deep", "test"])
    parser.add_argument("--model", default=MODEL)
    parser.add_argument("--backend", choices=["auto", "vllm", "hf"], default="auto")
    parser.add_argument("--chunk", type=int, default=2000, help="pairs per generate call (SAGEO: 2000)")
    parser.add_argument("--limit", type=int, default=0, help="score at most this many pairs per part (smoke test)")
    parser.add_argument("--batch_tokens", type=int, default=32768, help="hf backend: padded tokens per forward pass")
    args = parser.parse_args()
    in_dir, out_dir = Path(args.in_dir), Path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    LOG = out_dir / "progress.log"

    from transformers import AutoTokenizer
    tokenizer = AutoTokenizer.from_pretrained(args.model, revision=REVISIONS.get(args.model))
    tokenizer.padding_side = "left"
    prompts = Prompts(tokenizer)
    score = make_scorer(args.backend, args.model, prompts, args.batch_tokens)

    for part in args.parts:
        src, dst = in_dir / f"{part}.jsonl.gz", out_dir / f"{part}.jsonl"
        if not src.exists():
            log(f"{part}: no input, skipped")
            continue
        done = {json.loads(line)["key"] for line in open(dst)} if dst.exists() else set()
        total = sum(1 for r in read_part(src) if r["key"] not in done)
        if args.limit:
            total = min(total, args.limit)
        log(f"{part}: {total:,} pairs to score ({len(done):,} already done)")
        start, n, tokens, chunk = time.time(), 0, 0, []
        with open(dst, "a") as out:
            def flush():
                nonlocal n, tokens
                scores, t = score(chunk)
                out.write("".join(json.dumps({"key": r["key"], "score": s}) + "\n" for r, s in zip(chunk, scores)))
                out.flush()
                n, tokens = n + len(chunk), tokens + t
                rate = n / (time.time() - start)
                log(f"{part}: {n:,}/{total:,} pairs, {rate:.1f} pairs/s, {tokens / (time.time() - start):,.0f} tokens/s, "
                    f"ETA {(total - n) / rate / 60:.0f} min")
                chunk.clear()

            for r in read_part(src):
                if r["key"] in done:
                    continue
                if n + len(chunk) >= total:
                    break
                chunk.append(r)
                if len(chunk) == args.chunk:
                    flush()
            if chunk:
                flush()
    log("all parts done")


if __name__ == "__main__":
    main()
