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
