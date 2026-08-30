"""Public marketing landing page (Phase 96).

Served at ``GET /welcome`` — unauthenticated, unlike the app shell at ``/``.
Pricing is rendered directly from `apex_ai.billing.plans.PLANS` (Phase
81-84's real plan data) rather than hardcoded in the template, so this page
can never drift out of sync with what the product actually enforces.

Because billing integration itself is deliberately not connected (see
``docs/PHASE85_BILLING_INTEGRATION_DECISION.md``), every new account starts
on the Free plan regardless of which pricing card its owner looked at first
— the page says so plainly instead of implying a checkout flow that does
not exist.
"""

from __future__ import annotations

from html import escape

from apex_ai.billing.plans import PLANS, Plan, PlanLimits

_FEATURE_LABELS = {
    "priority_support": "Priority support",
    "dedicated_support": "Dedicated support",
}


def _format_price(price_cents: int) -> str:
    if price_cents == 0:
        return "Free"
    dollars = price_cents / 100
    return f"${dollars:.0f}/mo" if dollars == int(dollars) else f"${dollars:.2f}/mo"


def _format_count(value: int | None, unit: str, *, per_month: bool = False) -> str:
    if value is None:
        return f"Unlimited {unit}"
    display_unit = unit[:-1] if value == 1 and unit.endswith("s") else unit
    label = f"{value:,} {display_unit}"
    return f"{label}/month" if per_month else label


def _format_storage(mb: int | None) -> str:
    if mb is None:
        return "Unlimited storage"
    if mb >= 1000:
        gb = mb / 1000
        return f"{gb:.0f} GB storage" if gb == int(gb) else f"{gb:.1f} GB storage"
    return f"{mb} MB storage"


def _limit_lines(limits: PlanLimits) -> list[str]:
    return [
        _format_count(limits.max_documents, "documents"),
        _format_storage(limits.max_storage_mb),
        _format_count(limits.max_collections, "collections"),
        _format_count(limits.max_projects, "projects"),
        _format_count(limits.max_messages_per_month, "messages", per_month=True),
        _format_count(limits.max_tool_calls_per_month, "tool calls", per_month=True),
    ]


def _plan_card(plan: Plan) -> str:
    lines = "".join(f"<li>{escape(line)}</li>" for line in _limit_lines(plan.limits))
    feature_lines = "".join(
        f"<li>{escape(_FEATURE_LABELS.get(f, f))}</li>" for f in sorted(plan.features)
    )
    highlight = " plan-card-highlight" if plan.id == "pro" else ""
    return f"""
    <article class="plan-card{highlight}">
      <h3>{escape(plan.name)}</h3>
      <p class="plan-price">{_format_price(plan.price_cents)}</p>
      <ul class="plan-limits">{lines}{feature_lines}</ul>
      <a class="quiet-button plan-cta" href="/login">Get started</a>
    </article>
    """


def render_landing_html() -> str:
    plan_cards = "".join(_plan_card(PLANS[plan_id]) for plan_id in ("free", "pro", "business"))
    return f"""<!doctype html>
<html lang="en" data-theme="dark">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
  <meta name="color-scheme" content="dark light">
  <meta name="theme-color" content="#0b0d12">
  <meta name="description" content="Apex AI — private, source-grounded intelligence for your documents.">
  <title>Apex AI — Answers grounded in your documents</title>
  <link rel="icon" href="/assets/favicon.svg" type="image/svg+xml">
  <link rel="stylesheet" href="/assets/app.css">
  <link rel="stylesheet" href="/assets/landing.css">
</head>
<body>
  <header class="landing-nav">
    <a class="landing-brand" href="/welcome">
      <span class="brand-mark" aria-hidden="true">A</span>
      <span class="brand-name">Apex <strong>AI</strong></span>
    </a>
    <nav class="landing-nav-links">
      <a href="#features">Features</a>
      <a href="#pricing">Pricing</a>
      <a class="primary-button" href="/login">Sign in</a>
    </nav>
  </header>

  <main class="landing-main">
    <section class="landing-hero">
      <h1>Ask your documents. Get answers you can verify.</h1>
      <p class="landing-subtext">
        Generic AI chatbots hallucinate and can't be trusted with private files.
        Apex AI answers only from documents you upload, cites the exact source
        and page for every claim, and can run entirely offline — nothing
        leaves your server.
      </p>
      <div class="landing-cta-row">
        <a class="primary-button landing-cta" href="/login">Get started free</a>
        <a class="quiet-button landing-cta" href="#features">See how it works</a>
      </div>

      <div class="landing-preview" aria-hidden="true">
        <div class="landing-preview-topbar">
          <span class="brand-mark" aria-hidden="true">A</span>
          <span>Apex AI</span>
          <span class="landing-preview-online"><i></i>Online</span>
        </div>
        <div class="landing-preview-body">
          <div class="landing-preview-sidebar">
            <div class="new-chat-button"><svg viewBox="0 0 24 24"><path d="M12 5v14M5 12h14"/></svg><span>New chat</span></div>
            <div class="landing-preview-nav">
              <div class="sidebar-link active"><svg viewBox="0 0 24 24"><path d="M4 4h16v12H7l-3 3z"/></svg><span>Recents</span></div>
              <div class="sidebar-link"><svg viewBox="0 0 24 24"><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v8a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z"/></svg><span>Projects</span></div>
              <div class="sidebar-link"><svg viewBox="0 0 24 24"><path d="M4 6.5A2.5 2.5 0 0 1 6.5 4H10l2 2h5.5A2.5 2.5 0 0 1 20 8.5v8A2.5 2.5 0 0 1 17.5 19h-11A2.5 2.5 0 0 1 4 16.5z"/></svg><span>Documents</span></div>
              <div class="sidebar-link"><svg viewBox="0 0 24 24"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.7 1.7 0 0 0 .34 1.88l.06.06-2.83 2.83-.06-.06A1.7 1.7 0 0 0 15 19.4a1.7 1.7 0 0 0-1 .6 1.7 1.7 0 0 0-.4 1.1V21h-4v-.1A1.7 1.7 0 0 0 8.6 19.4a1.7 1.7 0 0 0-1.88.34l-.06.06-2.83-2.83.06-.06A1.7 1.7 0 0 0 4.6 15a1.7 1.7 0 0 0-.6-1 1.7 1.7 0 0 0-1.1-.4H3v-4h.1A1.7 1.7 0 0 0 4.6 8.6a1.7 1.7 0 0 0-.34-1.88l-.06-.06 2.83-2.83.06.06A1.7 1.7 0 0 0 9 4.6a1.7 1.7 0 0 0 1-.6 1.7 1.7 0 0 0 .4-1.1V3h4v.1A1.7 1.7 0 0 0 15.4 4.6a1.7 1.7 0 0 0 1.88-.34l.06-.06 2.83 2.83-.06.06A1.7 1.7 0 0 0 19.4 9c.12.38.34.72.64.98.3.25.68.4 1.06.42h.1v4h-.1A1.7 1.7 0 0 0 19.4 15z"/></svg><span>Settings</span></div>
            </div>
          </div>
          <div class="landing-preview-main">
            <p class="landing-preview-heading">What can I help with?</p>
            <div class="landing-preview-suggestions">
              <div class="suggestion"><span>Analyze my documents</span></div>
              <div class="suggestion"><span>Help me build an application</span></div>
              <div class="suggestion"><span>Research a topic</span></div>
              <div class="suggestion"><span>Explain complex information</span></div>
            </div>
            <div class="composer landing-preview-composer">
              <span class="composer-icon" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="m20.5 11.5-8.7 8.7a5 5 0 0 1-7.1-7.1l9.1-9.1a3.5 3.5 0 0 1 5 5l-9.1 9.1a2 2 0 0 1-2.8-2.8l8.5-8.5"/></svg></span>
              <span class="landing-preview-input">Message Apex AI…</span>
              <span class="send-button" aria-hidden="true"><svg viewBox="0 0 24 24"><path d="M12 19V5M6.5 10.5 12 5l5.5 5.5"/></svg></span>
            </div>
          </div>
        </div>
      </div>
    </section>

    <section class="landing-section" id="features">
      <h2>Built for documents you actually have to trust</h2>
      <div class="landing-feature-grid">
        <article class="landing-feature">
          <h3>Offline-first &amp; private</h3>
          <p>Inference, embeddings, and retrieval all run locally after setup.
          Your documents and conversations never have to leave your server.</p>
        </article>
        <article class="landing-feature">
          <h3>Hybrid retrieval</h3>
          <p>Semantic search and exact keyword matching run independently and
          are merged with weighted reciprocal rank fusion, then re-ranked —
          so both meaning and exact terms (IDs, dates, part numbers) are found.</p>
        </article>
        <article class="landing-feature">
          <h3>Honest citations</h3>
          <p>Every answer is built only from the evidence actually sent to the
          model, with source, page, and section on every claim. When the
          evidence isn't strong enough, Apex AI says so instead of guessing.</p>
        </article>
        <article class="landing-feature">
          <h3>Collections &amp; projects</h3>
          <p>Group documents into named knowledge bases, and group
          conversations into project workspaces with their own instructions
          and a linked collection that scopes retrieval automatically.</p>
        </article>
        <article class="landing-feature">
          <h3>Model-agnostic</h3>
          <p>Local GGUF (llama.cpp), Ollama, OpenAI-compatible APIs, or local
          Hugging Face models — swap the backend through configuration, not
          a rewrite.</p>
        </article>
        <article class="landing-feature">
          <h3>Built for real use</h3>
          <p>Verified, restorable backups; per-plan usage limits enforced on
          every upload and message; permissioned tool calling; structured
          outputs for the questions that need them.</p>
        </article>
      </div>
    </section>

    <section class="landing-section" id="pricing">
      <h2>Pricing</h2>
      <p class="landing-subtext landing-pricing-note">
        Every new account starts on the Free plan. Pro and Business are
        real, enforced tiers in the product today — self-serve upgrades to
        them require billing, which is not connected in this deployment yet.
      </p>
      <div class="landing-pricing-grid">{plan_cards}</div>
    </section>

    <section class="landing-section landing-final-cta">
      <h2>Try Apex AI on your own documents</h2>
      <a class="primary-button landing-cta" href="/login">Get started free</a>
    </section>
  </main>

  <footer class="landing-footer">
    <span>&copy; Apex AI</span>
  </footer>
</body>
</html>
"""
