from __future__ import annotations
import html, json, re
from urllib.parse import urlsplit
import httpx
from .models import WebSource
class PublicHttpClient:
    def __init__(self, *, transport=None): self.client=httpx.Client(timeout=12, transport=transport, follow_redirects=True, headers={"User-Agent":"Mindspace public reader/1.0"})
    def close(self): self.client.close()
    def get(self, url, **kwargs):
        if urlsplit(url).scheme not in {"http","https"} or (urlsplit(url).hostname or "").lower() in {"localhost","127.0.0.1","::1"}: raise ValueError("public HTTP(S) URL required")
        limit=kwargs.pop("max_bytes",0); response=self.client.get(url, **kwargs); response.read()
        if limit and len(response.content)>limit: raise ValueError("public response exceeds size limit")
        return response
class DocumentReader:
    def __init__(self,http): self.http=http
    def open_page(self,url):
        response=self.http.get(url); response.raise_for_status(); final=str(response.url); kind=response.headers.get("content-type","").lower(); raw=response.text if "pdf" not in kind else ""; title=re.search(r"<title[^>]*>(.*?)</title>",raw,re.I|re.S); text=re.sub(r"\s+"," ",html.unescape(re.sub(r"<[^>]+>"," ",raw))).strip()[:16000]
        return WebSource(source_type="pdf" if "pdf" in kind else "json" if "json" in kind else "rss" if "xml" in kind else "web_page", url=final, title=html.unescape(re.sub(r"<[^>]+>","",title.group(1))).strip()[:300] if title else "", text=json.dumps(response.json(),ensure_ascii=False)[:16000] if "json" in kind else text, freshness="static_page", evidence_level="public_page")
    def find_in_page(self,source,pattern):
        index=source.text.lower().find(pattern.lower()); return source.text[max(0,index-300):index+len(pattern)+900] if index>=0 else ""
