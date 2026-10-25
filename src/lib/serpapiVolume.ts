/**
 * Volume probe. Present so the live path is visible. Not called.
 * The key is read from .env. An empty value makes this return null and skip the fetch.
 */

export const CURSOR_VOLUME_PROMPT = `You are estimating how many similar student requests this call would surface. Do not hardcode the count.

The caller stopped at "data struc". Finish that tail with SerpAPI engine=google_autocomplete before you count anything.

Then run the finished query on each engine we expand with, not forums alone:
google, google_scholar, google_news, youtube, bing, duckduckgo, and reddit.
Read the count that engine actually returns (search_information.total_results, or the length of suggestions / related_searches / organic_results when a total is absent).

Predict a range from data/students.json, recomputed whenever the phrase list changes:
- low = records that match the literal unfinished query
- high = records that match after every engine's phrases are included
Do not invent the high number.

Do not call SerpAPI unless VITE_SERPAPI_KEY is set in .env. Leave predictSimilarRequests uncalled and use the local expansion comparison.`

export const VOLUME_PROBE_SOURCE = `export async function predictSimilarRequests(query: string) {
  const apiKey = import.meta.env.VITE_SERPAPI_KEY ?? ''
  if (!apiKey) return null
  const params = new URLSearchParams({
    engine: 'google',
    q: query,
    api_key: apiKey,
  })
  const response = await fetch('https://serpapi.com/search.json?' + params)
  const body = await response.json()
  const total = body.search_information?.total_results ?? 0
  return { totalResults: total }
}`

export async function predictSimilarRequests(query: string): Promise<{ totalResults: number } | null> {
  const apiKey = import.meta.env.VITE_SERPAPI_KEY ?? ''
  if (!apiKey) return null
  const params = new URLSearchParams({
    engine: 'google',
    q: query,
    api_key: apiKey,
  })
  const response = await fetch(`https://serpapi.com/search.json?${params}`)
  const body = (await response.json()) as { search_information?: { total_results?: number } }
  return { totalResults: body.search_information?.total_results ?? 0 }
}
