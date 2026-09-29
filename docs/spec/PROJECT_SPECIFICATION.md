# Project Specification

## Product Scope

This project produces controlled mathematics teaching documents for primary, middle, high-school, and transition-stage teaching across supported textbook versions. Supported document types are classroom handouts, exercises and homework, examinations with answers and scoring guidance, student knowledge cards, teacher knowledge records, and learning-analysis templates.

The product is a controlled supply-and-delivery system, not a generic AI content generator. It must preserve trusted source-question content and demonstrate the evidence required to use that content in a formal delivery.

## Current First Vertical Slice

The first vertical slice is one versioned Delivery Card whose requested buckets are satisfied from canonical QSR-bound supply, pass all applicable gates, render into a reviewable teaching document, and become formal delivery only after delivery evidence is recorded. The slice must fail closed when any bucket is insufficient or evidence is incomplete.

## Users

- Teachers and instructional operators requesting a bounded teaching document for a class.
- Content and supply operators maintaining trusted source intake and evidence.
- Delivery reviewers who verify the controlled output before formal delivery.
- Project maintainers who audit provenance, eligibility, and same-class reuse history.

## Outputs

- A versioned Delivery Card and bucketed supply result.
- A controlled teaching document built from eligible, source-faithful QSRs.
- Review, rendering, delivery, and same-class usage evidence where the delivery reaches that state.
- A blocking report when safe supply or required evidence is unavailable.

## Non-Goals

- Generating new source questions with AI or silently rewriting trusted source questions.
- Treating legacy databases, candidate-only records, rough samples, browser output, or KPLC records as formal delivery inventory without the contract evidence.
- Crossing textbook, school-stage, topic, or Delivery Card bounds to fill a shortage.
- Declaring production readiness from a test, sample, historical report, or worktree alone.

## Acceptance Outcomes

- Every selected question is traceable through its canonical QSR to source and parse/import evidence.
- Every Delivery Card bucket has an explicit sufficient, insufficient, blocked, or pending result.
- Candidate-only evidence is excluded from approval, selection, delivery, and same-class usage history.
- A formal delivery has evidence for its gate results, rendering/review outcome, delivery state, and class usage effect.
- An evidence gap blocks the delivery rather than being filled by unsupported assumptions.

## Fact and Uncertainty Boundary

Observed during GOVERNANCE-RESET-001: the project has legacy root code and registered browser-collection worktrees; historical documents describe rough lecture, collection, isolated import, and KPLC work.

Historical-report claims were not re-run by this documentation review. Future work orders must measure source fidelity, QSR binding, supply sufficiency, rendering, delivery, and reuse behavior. The user reserves decisions on source acquisition policy, rollout scope, and acceptance of any temporary operational exception.
