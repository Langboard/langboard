# Langboard interface polish — 2026-11-12

User-approved direction: retain the logo, typeface, light and dark themes; make the interface calm, exact and dense. Purple identifies selection and primary actions. Product content, user labels, workflow meanings, routes and permissions remain unchanged. Desktop and mobile are both in scope.

This work applies Unslop by Damir Sokolovsky (@heydamir), CC BY 4.0. It adapts the spacing, quiet-chrome, type hierarchy and purposeful-motion guidance to the existing Langboard components; it does not introduce a component framework or generated imagery.

## Source scope

Company integration baseline: `8c1b7d947c093b665399fbd9525972fe29f6f184`. Split upstream UI baseline: `c4a7c9aebce54d3de62e9e8a933d29096a2842d6`. The upstream patch uses the owning atoms/molecules/organisms/templates paths. Company-only Workbench, app catalogue and board signal refinements remain in the company integration patch; they are not copied into upstream as replacement features.

## Audit and changes

- Spacing: align account/auth action groups and page titles; wrap project filters on narrow viewports; use 36px desktop controls with at least 44px height for coarse pointers. Existing explicit touch controls are retained.
- Color: neutralize blue-black surfaces and use a single low-saturation neutral ramp; preserve the purple accent and semantic workflow/label colors. Dark primary controls use dark foreground ink for contrast.
- Icons: use the existing Lucide set with a 1.5 default stroke; explicit stroke overrides and country flags retain their behavior. User-authored emoji labels are content and are preserved.
- Copy: retain translations and user content. Error messages stop forced uppercasing. No new product claims or synthetic operational data.
- Type: consistent 24px account/settings titles, readable line height, quiet medium-weight project names and tabular table figures. Existing fonts remain.
- Layout: preserve page sections and navigation behavior; improve filter wrapping and account/auth form rhythm without reparenting editors or panels.
- Details: quieter cards and badges, distinct table header/hover/focus/selection states, less decorative sidebar controls and app-store installation headers.
- Visuals: actual work status and content carry color. Remove app installation header gradient and card urgency glow; retain static semantic urgency borders, privacy patterns and readable priority labels.
- Motion: remove perpetual urgency shaking/pulsing and presentation-only `transition-all`/hover enlargement. Preserve meaningful live-agent indicators, loading states and established editor transitions. Chevron changes name transform explicitly and respect reduced motion.

## Route inventory

The inventory includes redirects and modal declarations; counts are not distinct rendered screens. All families inherit the shared tokens and controls. Changed-file checks do not prove each runtime route.

| Route owner | Path declarations | Source coverage |
| --- | ---: | --- |
| `AccountPage/Route.tsx` | 9 | Shared system; relevant page-specific refinements |
| `AppsPage/Route.tsx` | 2 | Shared system; relevant page-specific refinements |
| `BoardPage/Route.tsx` | 10 | Shared system; relevant page-specific refinements |
| `DashboardPage/Route.tsx` | 8 | Shared system; relevant page-specific refinements |
| `ErrorPage/Route.tsx` | 2 | Shared system; relevant page-specific refinements |
| `HomePage/Route.tsx` | 2 | Shared system; relevant page-specific refinements |
| `SettingsPage/Route.tsx` | 30 | Shared system; relevant page-specific refinements |
| `auth/AccountRecoveryPage/Route.tsx` | 2 | Shared system; relevant page-specific refinements |
| `auth/SignInPage/Route.tsx` | 3 | Shared system; relevant page-specific refinements |
| `auth/SignUpPage/Route.tsx` | 7 | Shared system; relevant page-specific refinements |

## Verification boundaries

Initial authenticated IAB audit inspected the project dashboard, board and settings/API-key list, at the default narrow viewport and a 1440×900 desktop viewport. These were baseline screens, not evidence of the changed build.

Local changed-file ESLint passed. Tailwind/PostCSS generated the real stylesheet successfully using the existing dependencies. Theme calculations passed 4.5:1 for primary text, muted text, primary text against the background and primary-button foreground in both themes (lowest measured pair: light muted text, 5.41:1). The unchanged Markdown editor regression suite passed 15/15 on installed Plate53.3.15.

A temporary direct-source Core alias typecheck was invalid because the shared package uses its own `@/` aliases. Its preparation diagnostics are retained locally; it is not application qualification. Current Core build plus whole-app typechecking must run in the existing browserless company workflow. No new browser or dependency installation occurred.

Pending acceptance: exact-source CI, normal development-canary integration/deployment, page-family navigation in both themes, 390px/desktop checks, keyboard states, actual changed-interface recording, existing-card upload/body placement and verified playback/seeking. A source diff, upload response or screenshot alone does not complete those gates. No main merge or manual deployment is included.
