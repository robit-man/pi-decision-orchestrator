# Observable interaction contract

Authority: PRODUCT.md and the supplied work order. Visual ownership: DESIGN.md.

## Canonical UI map

| Capability | Canonical owner | Source | Variants | Verification |
|---|---|---|---|---|
| Select/Listbox | ui.tsx Select | DESIGN.md | Native select; platform popup accepted | keyboard + browser |
| Form | ui.tsx Field and forms.tsx SchemaForm | API schemas | create/edit/live input | validation + browser |
| Scrollbar | style.css global baseline | DESIGN.md | natural document, table/canvas internal overflow | computed style |
| Toast | ui.tsx notification system | This contract | success/warning/error | live region |
| CRUD | API client and page owners | Versioned API | ordinary create returns list; builder draft stays in editor | browser full flows |
| Date | Formatted read-only timestamps | Browser locale | No calendar inputs needed | formatting |
| Table Selection | No bulk destructive selection | This contract | Individual explicit actions | browser |

## Common behavior

Protected screens require a session. Viewer reads; operator builds/tests/executes decisions and manages datasets; administrator manages models, connections, credentials, users and server settings. External actions require both workflow-action authorization and administrator-approved output destinations. Confirmation names the destination and the effect. API authorization remains definitive.

Creates return to the owning list and announce success. The builder intentionally stays after saving its draft, because continued editing/testing is the primary workflow. Published versions are immutable; edits affect the draft. Optimistic revision checks return recoverable conflicts without clearing input. Incomplete drafts are saved with visible validation errors; publication and execution require validation.

Dialogs are app-owned native dialog elements, with accessible titles, cancellation, focus placement and restoration. Destructive deletion, token creation and external-action permission changes require explicit confirmation. Saves do not. Unsaved in-app navigation is guarded; browser unload uses beforeunload only when dirty. A shared status/live region reports mutations; errors remain inline near their task.

Lists are server-paginated; search/filter/page live in URL query parameters. Short bounded model catalogs and node-type lists render fully. Search clears immediately, debounces remote requests by 300ms, respects composition, and cancels stale requests. Empty, no-results, loading, offline, server error and permission states give a next action. Long forms retain natural scrolling.

Sensitive inputs are masked with a show/hide control. Connection secrets are write-only and never repopulated; blank secret fields preserve stored credentials only when explicitly indicated. API tokens are displayed once after an administrator creates them, then stay only in component memory. No credentials are persisted in browser storage, URLs or logs. Sessions are HTTP-only cookies; CSRF values remain in memory.

Live testing defaults to simulation. Continuous tests wait for the current execution to finish, then wait at least five seconds. Pause stops scheduling; stop also cancels the current execution. Comparisons remain serialized by the server. Every full distribution is visible; generative/tool results explicitly state probabilities are unavailable.

Keyboard focus is visible. Dragging nodes has move/connect form alternatives. English is the supported interface language; dates and numbers use the browser's locale and timestamps include local timezone information. Dark/light theme preferences may use localStorage because they are not sensitive. Reduced motion disables nonessential transitions. Mobile retains all fields and actions through scrolling and labeled navigation.
