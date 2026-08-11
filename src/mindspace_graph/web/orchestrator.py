from __future__ import annotations
from concurrent.futures import ThreadPoolExecutor,as_completed
from .models import WebQuery,WebResult
from .policy import finalize_result
from .providers import GitHubProvider, SearchProvider, SocialProvider, compact_failure
from .ranking import rank_sources
from .readers import DocumentReader,PublicHttpClient
from .routing import infer_platforms
from .weather import WeatherProvider, is_weather_query, weather_location
class WebOrchestrator:
    def __init__(self,*,config_provider,http_transport=None): self.config_provider=config_provider;self.http=PublicHttpClient(transport=http_transport);self.reader=DocumentReader(self.http);self.search_provider=SearchProvider(self.http,config_provider);self.github=GitHubProvider(self.http);self.social=SocialProvider(self.http,self.search_provider);self.weather=WeatherProvider(self.http)
    def close(self):self.http.close()
    def open_page(self,url):return self.reader.open_page(url)
    def find_in_page(self,source,pattern):return self.reader.find_in_page(source,pattern)
    def _social(self,query,platform):
        try:
            values,provider=self.social.search(query,platform); return values,[],[provider]
        except Exception as exc:
            return [], [compact_failure(platform, exc)], []
    def execute(self,query):
        original_intent = query.original_intent or query.query
        platforms = list(dict.fromkeys([*infer_platforms(original_intent), *query.platforms]))
        query = query.model_copy(update={"original_intent": original_intent, "platforms": platforms})
        if query.action=="open_page":
            try:return finalize_result(query,[self.open_page(query.url or query.query)],[], executed=True)
            except Exception as exc:return finalize_result(query,[],[{"provider":"open_page","error":str(exc)[:300]}], executed=False)
        if is_weather_query(query) and weather_location(query):
            try:
                return finalize_result(query,self.weather.search(query),[],executed=True)
            except Exception:
                # Search pages remain a bounded fallback when the structured
                # provider cannot resolve a place or is temporarily offline.
                pass
        github_requested = "github" in query.platforms or query.scope == "developer"
        jobs = [("github", lambda: (self.github.search(query), [], ["github_rest"]))] if github_requested else [("search", lambda: self.search_provider.search(query))]
        for platform in query.platforms:
            if platform!="github":jobs.append((platform,lambda platform=platform:self._social(query,platform)))
        sources=[];failures=[];successful_providers=[]
        with ThreadPoolExecutor(max_workers=min(4,len(jobs))) as pool:
            for future in as_completed([pool.submit(job) for _,job in jobs]):
                try:
                    found,failed,providers=future.result();sources.extend(found);failures.extend(failed);successful_providers.extend(providers)
                except Exception as exc: failures.append(compact_failure("platform", exc))
        unique_failures = list({(item.get("provider", ""), item.get("error", "")): item for item in failures}.values())
        settings=(self.config_provider().get("capabilities")or{}); ranked=rank_sources(query,sources,limit=max(1,min(20,int(settings.get("max_web_results",10))))); return finalize_result(query,ranked,unique_failures, executed=bool(successful_providers))
