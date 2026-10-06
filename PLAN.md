# VoRoute site plan

Planning only. No page other than this document should change until this is reviewed.

`site/PLAN.md` sits inside the directory Cloudflare Pages deploys. A later `wrangler pages deploy site` would publish it at `/PLAN.md`. Move or exclude it before the next production deploy.

## 1. Current state

`site/index.html` is the entire public site. One HTML file, no JavaScript, no external stylesheet, no web fonts. All CSS is a single `<style>` block in the head. The favicon is an inline SVG data URI.

The page is a column, `.wrap`, max width 980px, horizontal padding 24px. Top to bottom:

1. Header (`.top`): mono wordmark `VOROUTE`, and a status tag (`.status` + amber `.dot`) reading "In development". The wordmark is not a link. There is no navigation.
2. Hero (`.hero`): an `h1` ("Voice confirmation for COD orders.") and one muted `.sub` line about RTO being an intent problem.
3. Stat strip (`.strip`): three cells — `20–35%` / COD orders return, `Before dispatch` / confirmation window, `Hindi · Hinglish` / on the call.
4. `01 — The problem` (`.problem`): one paragraph, then a `.stat` repeating `20–35%`.
5. `02 — How it works`: a four-step `<ol class="flow">` — order placed, VoRoute calls, confirms intent, ship or hold. Phosphor step numbers (`.n`), faint arrows (`.arr`).
6. `03 — The system` (`.system`): one sentence on real-time voice, then a `.pipe` of `.chip` labels (`STT → Intent → TTS · Order store`).
7. Footer: "Built by Ayush Mishra", plus GitHub and LinkedIn. No email. No other pages.

CSS approach: custom properties on `:root` for color only. Type, spacing, and layout are raw values on element and class selectors, not tokens. The headline is the system sans stack, not a serif. Mono is applied per component (wordmark, kicker, strip, stat numeral, chips, footer), and a `.mono` class exists but is unused in the markup. One breakpoint, `max-width: 760px`, stacks the strip and the flow and hides the arrows.

The Worker in `site-proxy/worker.js` does not render HTML. It forwards the request path and query to `https://voroute.pages.dev`. `wrangler.toml` attaches that Worker to `voroute.com/*` and `www.voroute.com/*`.

## 2. Proposed pages

Build three pages. Do not build a contact page.

### `/about`

Purpose: say who operates VoRoute, in the same voice as the homepage. Reviewers looking for a real project (Twilio included) need a person and a way to reach them. The homepage already explains the product; this page should not repeat the flow diagram or the stat strip.

URL: `/about`

Sections:

- Same header and footer as every page.
- Kicker `About`, then a short `h1` (a sentence, not a slogan stack).
- One paragraph: VoRoute places outbound voice calls to confirm COD orders for Indian D2C brands before dispatch. Status stays "in development".
- One paragraph: built by Ayush Mishra. Link GitHub and LinkedIn with the same URLs as the footer.
- Contact line: `support@voroute.com` as a `mailto:` link. No form, no address block, no phone number.

### `/privacy`

Purpose: a real policy for a site that collects nothing, and for a product that will place calls using a phone number the merchant already has. Short enough to be true.

URL: `/privacy`

Sections:

- Kicker `Privacy`, `h1` "What this site keeps."
- The public site: no accounts, no forms, no analytics, no cookies set by VoRoute.
- The product, stated as intent rather than a live claim: a merchant will send an order (including a customer phone number) so VoRoute can place a confirmation call. That number is used to make the call and record the outcome (confirmed, declined, no answer, address fix). It is not sold.
- Contact: `mailto:support@voroute.com` for any request about that data.
- A plain "last updated" date in the mono kicker style. No legal theater, no fake certifications.

### `/terms`

Purpose: the matching public terms a vendor review expects next to privacy. Same length and honesty.

URL: `/terms`

Sections:

- Kicker `Terms`, `h1` "Using VoRoute."
- The site is informational. The call product is in development and not offered as a self-serve service from this page.
- When a merchant later sends orders, they are responsible for having the right to contact that customer about that order.
- No warranty, no uptime claim, governing contact is `support@voroute.com`.
- Same "last updated" line as privacy.

### Contact — not its own page

Put `support@voroute.com` in the shared footer as `mailto:support@voroute.com`, and repeat it on `/about`, `/privacy`, and `/terms`. A `/contact` page that only holds one mailto link is a empty room. The address is public; the footer is the contact point.

### Do not build yet

- `/pricing`, `/signup`, `/login`, `/app`, `/dashboard`. There is no offer and no product to sign into. Any of these would read as a funnel.
- `/customers`, logo rows, case studies. None exist.
- `/blog`, `/docs`, `/changelog`. Nothing public to document yet.
- `/status`. The header tag already says in development. A status page implies an operating service.
- `/careers`.
- A second "how it works" or architecture page. Section 02 and 03 on the homepage are the whole explanation worth publishing.

## 3. Shared structure

Keep the site static. Do not add a generator, a templating step, or Worker-injected HTML.

Extract the shared CSS from `index.html` into `site/styles.css`. Every page, including the homepage, loads it with `<link rel="stylesheet" href="/styles.css">`. Page-specific layout does not get a new file. Privacy and terms are normal paragraphs inside the existing `section` rhythm; about is the same. If a rule is only used by the homepage (`.strip`, `.flow`, `.stat`, `.pipe`), it still lives in `styles.css`. The file is small, and splitting it would be the start of a design system nobody needs.

Duplicate the header and footer markup by hand on each HTML file. Four copies of that block is smaller than a build. The canonical markup:

- Header: `.top` inside `.wrap`. The wordmark is an `<a class="wordmark" href="/">VOROUTE</a>`. The status tag stays, unchanged, on every page.
- Footer: the current credit and social links, plus About, Privacy, Terms, and `mailto:support@voroute.com`. Same order on every page.

No header nav. The homepage should not grow a menu. Inner pages are reached from the footer, which is where a reviewer looks for privacy and terms.

Do not introduce a serif, a webfont, or a second accent color while extracting CSS. The extraction is a move, not a redesign. The homepage must look the same after the stylesheet moves.

## 4. Style preservation

There is no serif on the live page. The `h1` inherits the body sans. Do not add one.

### Fonts

Body, and therefore every `h1`:

`ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif`

Body size `17px`, line-height `1.5`, letter-spacing `-0.011em`. `h1` weight `500`, letter-spacing `-0.035em`, line-height `1.02`, size `clamp(2.05rem, 5vw, 3.35rem)`, margin `0`, max-width `16ch` on the homepage only. Inner-page headings may use a wider max-width so a short sentence does not break awkwardly; they use the same size, weight, and tracking.

Mono stack, already repeated on the wordmark, status, kicker, strip, stat numeral, flow numbers, arrows, chips, and footer:

`ui-monospace, "SF Mono", "Cascadia Mono", Menlo, Consolas, monospace`

`.mono` is defined and unused. New pages should use the existing component classes, not start sprinkling `.mono`.

### Color variables

| Token | Value |
| --- | --- |
| `--bg` | `#0c0d0c` |
| `--ink` | `#e8e6df` |
| `--muted` | `#8f8d84` |
| `--faint` | `#5e5c55` |
| `--line` | `rgba(232, 230, 223, 0.12)` |
| `--line-strong` | `rgba(232, 230, 223, 0.28)` |
| `--amber` | `#e3a008` |
| `--phosphor` | `#c6f54e` |

Also fixed, not tokenized: `color-scheme: dark`, `theme-color` `#0c0d0c`, focus ring `1px solid var(--phosphor)` with `outline-offset: 3px`, dot halo `0 0 0 4px rgba(227, 160, 8, 0.14)`. Links inherit `--ink` except footer links, which are `--muted` and turn `--ink` on hover. No underlines except that hover.

### Spacing and measure

- `.wrap` max-width `980px`, padding `0 24px`.
- Header padding `22px 0 18px`, gap `16px`, bottom border `--line`.
- `.hero` padding `64px 0 48px` (at `760px`: `40px 0 32px`).
- `.sub` margin-top `28px`, max-width `36rem`, size `1.125rem`, color `--muted`.
- `section` padding `56px 0`, bottom border `--line`.
- `.kicker` size `12px`, tracking `0.16em`, uppercase, color `--faint`, margin `0 0 18px`.
- Prose measure: problem copy max-width `40rem` at `1.2rem`; system copy max-width `38rem`. New pages use the system measure (`38rem`) and body size, not a new type scale.
- Footer padding `28px 0 48px`, size `12px`, tracking `0.02em`, color `--faint`.
- Breakpoint stays `760px`. New pages do not add a second breakpoint.

### Classes every new page reuses

`wrap`, `top`, `wordmark`, `status`, `dot`, `kicker`, `section` (the element), and `footer`. Inner pages do not reuse `.strip`, `.flow`, `.stat`, `.pipe`, or `.chip` unless a later page actually has that kind of content. About, privacy, and terms are prose.

Homepage-only classes that must survive the extraction unchanged: `hero`, `sub`, `strip`, `problem`, `stat`, `flow`, `n-row`, `arr`, `n`, `system`, `pipe`, `chip`.

## 5. Routing

Cloudflare Pages maps a directory index to a clean path. Add:

- `site/about/index.html` → `/about` and `/about/`
- `site/privacy/index.html` → `/privacy` and `/privacy/`
- `site/terms/index.html` → `/terms` and `/terms/`
- `site/styles.css` → `/styles.css`
- `site/index.html` stays `/`

Do not use `about.html`. That publishes `/about.html`, not `/about`.

The Worker does not need a change. It already sends `pathname + search` to `voroute.pages.dev`, and both `voroute.com/*` and `www.voroute.com/*` are routed. `/about` on the apex and on `www` will follow the path through.

No `_redirects` file. No trailing-slash rewrite in the Worker. No new route patterns.

## 6. Build order

Each step ships on its own. Stop after any step and the live site is still coherent.

1. Move the `<style>` block into `site/styles.css` and link it from `index.html`. No markup change except the wordmark becoming a link to `/` only if the footer links from step 2 are not ready — otherwise leave the header alone in this step. Deploy. Confirm the homepage matches what is live now.
2. Add `/privacy` using the shared header, footer, and stylesheet. Add Privacy and `mailto:support@voroute.com` to the footer on the homepage and on the privacy page. Deploy.
3. Add `/terms` the same way. Add Terms to both footers that already exist, and to this page. Deploy.
4. Add `/about`. Add About to every footer. The mailto is already in the footer from step 2; the about page repeats it in the body. Deploy.

Do not start step 2 until step 1 has been looked at in the browser. The risk in step 1 is a path or cache mistake, not a design change.
