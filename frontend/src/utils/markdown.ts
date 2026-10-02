// Markdown -> sanitized HTML with syntax highlighting for chat bubbles.
import DOMPurify from 'dompurify'
import hljs from 'highlight.js/lib/common'
import { marked, Renderer } from 'marked'
import 'highlight.js/styles/github.css'

const renderer = new Renderer()

renderer.code = function ({ text, lang }: { text: string; lang?: string }) {
  const language = lang && hljs.getLanguage(lang) ? lang : undefined
  const highlighted = language
    ? hljs.highlight(text, { language }).value
    : hljs.highlightAuto(text).value
  const cls = language ?? 'plaintext'
  return `<pre><code class="hljs language-${cls}">${highlighted}</code></pre>`
}

marked.use({ renderer, breaks: true, gfm: true })

/** Render untrusted LLM/Markdown text into safe HTML for v-html. */
export function renderMarkdown(source: string): string {
  const html = marked.parse(source ?? '', { async: false }) as string
  return DOMPurify.sanitize(html, {
    ADD_ATTR: ['target'], // links open via the global click handler below
  })
}
