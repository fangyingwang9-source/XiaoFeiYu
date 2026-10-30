# -*- coding: utf-8 -*-
"""微信读图：智谱 GLM-4V-Flash 优先，Ollama 兜底。"""
import base64, json, os, time
import urllib.request

ZHIPU_URL = "https://open.bigmodel.cn/api/paas/v4/chat/completions"
_UA = "wechat-bridge/1.0"


def _b64(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode()


def _post_json(url, payload, headers, timeout):
    req = urllib.request.Request(url, data=json.dumps(payload).encode(), headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def glm_vision(path, prompt, model="glm-4v-flash", timeout=40):
    key = os.environ.get("ZHIPU_API_KEY", "").strip()
    if not key:
        raise RuntimeError("ZHIPU_API_KEY 未设置")
    mime = "image/png" if path.lower().endswith(".png") else "image/jpeg"
    payload = {
        "model": model,
        "messages": [{"role": "user", "content": [
            {"type": "image_url", "image_url": {"url": "data:%s;base64,%s" % (mime, _b64(path))}},
            {"type": "text", "text": prompt},
        ]}],
        "temperature": 0.2,
    }
    out = _post_json(ZHIPU_URL, payload, {"Authorization": "Bearer " + key, "Content-Type": "application/json"}, timeout)
    return out["choices"][0]["message"]["content"]


def ollama_vision(path, prompt, model="qwen2.5vl:3b", timeout=90):
    payload = {"model": model, "prompt": prompt, "images": [_b64(path)], "stream": False}
    out = _post_json("http://127.0.0.1:11434/api/generate", payload, {"Content-Type": "application/json"}, timeout)
    return out.get("response", "")


def describe(path, prompt="用一句中文描述这张图里有什么，20字以内。", model="glm-4v-flash"):
    t0 = time.time()
    try:
        txt = glm_vision(path, prompt, model)
        return {"ok": True, "via": "glm:" + model, "text": txt.strip(), "secs": round(time.time() - t0, 2)}
    except Exception as e:
        err = repr(e)[:200]
    try:
        txt = ollama_vision(path, prompt)
        return {"ok": True, "via": "ollama", "text": txt.strip(), "secs": round(time.time() - t0, 2)}
    except Exception as e2:
        return {"ok": False, "via": "none", "error": err + " | ollama: " + repr(e2)[:120], "secs": round(time.time() - t0, 2)}


if __name__ == "__main__":
    import sys
    p = sys.argv[1] if len(sys.argv) > 1 else r"E:\useer\deepseek-harness\default-workspace\wechat-bridge\\_wx_now.png"
    print(json.dumps(describe(p), ensure_ascii=False, indent=1))
