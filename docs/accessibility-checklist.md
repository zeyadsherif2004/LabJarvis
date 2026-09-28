# Accessibility checklist

Automated tests scan setup and sterile mode for serious/critical WCAG 2.0/2.1 A/AA axe findings and component tests cover semantic warnings/non-color recovery. Automated success does not establish conformance. Complete and date the manual rows with the browsers/assistive technologies actually used before a participant study or final claim.

**Every manual row below is still outstanding as of 27 September 2026.** (Automated E2E checks added on 2026-09-27: 44 px minimum targets for sterile-mode buttons, inputs and selects; no horizontal scroll at 320 CSS px; visible focus indicator on keyboard focus; reduced-motion removes animation; hands-free journey by key + voice only. Two defects found by these checks were fixed: a 27 px timer input and a 43 px select.) No
keyboard walkthrough, screen-reader pass, contrast measurement, zoom check, or
device-permission test has been performed by a person. The automated column
records what is actually covered by a test; the manual column is not a
prediction of what such a test would find. Do not mark a row complete without
recording the date, browser, device, assistive technology, and the issues found.

| Area | Check | Implementation / test | Manual status |
|---|---|---|---|
| Structure | One logical heading hierarchy; landmarks/regions have useful names | semantic React views; Playwright role locators | Awaiting manual review |
| Keyboard | All import/edit/capture/confirm/reject/export controls reachable and operable; no trap | native controls and dialogs | Awaiting keyboard walkthrough |
| Focus | Visible focus; focus moves to dialogs/errors and returns sensibly | CSS focus styles; `accessibility.spec.ts` asserts a visible outline on keyboard focus | Awaiting browser/AT review |
| Labels | Inputs, icon buttons, device controls, statuses, and progress have accessible names | Testing Library role/name queries | Awaiting screen-reader review |
| Color | Match/mismatch/uncertain and errors include text/icon/shape, never color alone | component and E2E assertions | Awaiting visual review |
| Contrast | Text, controls, focus indicators, warning panels meet intended AA contrast | high-contrast token system | Awaiting measured contrast audit |
| Targets | Primary sterile controls remain large enough at bench distance/touch | `accessibility.spec.ts` asserts >= 44x44 CSS px for every sterile-mode button/input/select | Awaiting device measurement |
| Zoom/reflow | 200% zoom and 320 CSS-pixel width preserve content/operation without two-dimensional scroll | `accessibility.spec.ts` asserts no horizontal scroll at 320 px (zoom not automated) | Awaiting Chrome/Firefox check |
| Motion | Reduced-motion preference removes nonessential transition/animation | `prefers-reduced-motion` CSS; `accessibility.spec.ts` emulates reduced motion and asserts no running animation | Awaiting OS preference check |
| Audio alternatives | Every spoken message is visible; mute/replay available | shared grounded message, UI controls, and the hands-free panel writes out every sentence it was asked to speak | Awaiting manual check |
| Voice fallback | Hands-free failure never removes a capability | `useRealtimeVoice` reconnect-exhaustion test asserts the message names push-to-talk; spoken confirmation refusal test asserts the on-screen control stays available | Awaiting manual check |
| Route recovery | A missing session or protocol gives a named state with a way out, not a blank or a stale screen | `App.routes.test.tsx` and `route-identity.spec.ts` | Awaiting screen-reader announcement check |
| Save state | Autosave status is announced and distinguishes saving, saved, failed, and conflict by wording as well as colour | `ProtocolReviewView.test.tsx`; `role="status"` region | Awaiting screen-reader review |
| Media permission | Denial is explained and typed/upload fallback remains | component + failure E2E | Awaiting real device denial check |
| Timing | Microphone auto-stop is visible/predictable; no task timeout loses records | push-to-talk capped at 30 s with Escape to cancel; hands-free listener idles off after 10 min **outside a session** and says so; during a session it stays on (ADR 0018) | Awaiting cognition/usability review |
| Errors | Problem stated in plain language with recovery; correlation ID safe to share | Problem Details + banners | Awaiting screen-reader announcement check |
| Forms | Protocol edits preserve labels/instructions and validation identifies the field | semantic form controls | Awaiting complex-edit workflow |
| Data dialogs | Typed destructive confirmations trap focus, close with Escape, restore focus, and preserve the phrase on failure | `DataManagementView.test.tsx`; Playwright accessibility journey | Awaiting keyboard/screen-reader review |
| Discovery filters | State, label, protocol-version, date, and archive controls have programmatic labels and remain operable while refresh status is announced | `DashboardView.test.tsx`; native form controls | Awaiting keyboard/zoom review |
| Scanned import | OCR is an explicit labelled choice and OCR-derived text is disclosed before review | `SetupView.test.tsx`; protocol source semantics | Awaiting screen-reader review |
| Tables/lists | Notebook/model evidence has headings and remains readable at zoom | semantic sections/cards | Awaiting AT review |
| Voice-only | The exact reply to say ("Jarvis, confirm ...") is shown as text next to each proposal and reading; refusals are spoken and shown; live-scan progress and prompts are in a `role=status` region, and a timeout in a `role=alert` | `voicePhrase.test.ts`; `LiveScan.test.tsx`; `test_voice_only_confirmation.py` | Awaiting manual check (NVDA + real microphone) |
| Hands-free | Push-to-talk key/pedal, spoken read-back confirmation, capture countdown and listening state are announced and visible | `hands-free.spec.ts`; `HandsFree.test.tsx`; `HandsFreeListeningPanel.test.tsx` (`role=status` regions) | Awaiting NVDA + physical pedal check |
| Speech | Wake phrase is optional; typed use supports speech impairments/accents/noisy spaces | setup option and typed input | Awaiting representative user study |
| Eyes-free | Every outcome is spoken (proposals with the phrase to say, commits, refusals, errors, scan prompts, label results, timer events, answers); nothing is only visible; spoken text never contains the wake phrase; brief/full detail | `speechOutput.ts`; zero-touch E2E asserts every step by what was spoken; `HandsFreeLifecycle.test.tsx` | Awaiting NVDA + real-voice rehearsal (H7) |
| Alarms | A finished timer is announced by speech, tone and a text banner with an icon (not colour alone), repeated until acknowledged; survives reload | `timerAlarms.ts`, `TimerPanel` alarm banner test, backend alarm persistence test | Awaiting manual check of tone audibility in a noisy room |
| One-tap start | The single required gesture is one large target shown before gloving up; kiosk launch removes it on localhost and says so on screen | `HandsFreeStartPrompt.tsx`; zero-touch and kiosk E2E | Awaiting manual check |
| Listener recovery | A microphone or detector failure is spoken and retried (3 attempts, backoff) before asking for a tap | `HandsFreeLifecycle.test.tsx` | Awaiting unplug/revoke manual check |

## Suggested manual protocol

1. Chrome and Firefox: keyboard-only full principal journey, visible focus, 200% zoom, narrow viewport, high contrast, reduced motion.
2. NVDA on Windows (or an approved equivalent): setup, protocol review, command confirmation, mismatch warning, correction, export; record announcements/order.
3. Real permission denial/revocation for camera and microphone.
4. Touch device or trackpad at bench distance with gloves simulated only if ethically/safely appropriate—never during hazardous work.
5. Record each issue with its date, affected task, fix, commit, and retest in
   the study results. Avoid claiming WCAG conformance without a qualified
   audit.

