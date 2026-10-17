# Common mobile activity rail

The existing `ActivityRail` owns both desktop and mobile navigation. Desktop keeps
its 48px left rail. Below `md`, the same item list supplies a 56px bottom row plus
its border and device safe area. The Header does not render a duplicate navigation
drawer when the page registers an activity rail.

`IActivityRailItem.mobile` optionally identifies primary navigation, app shortcuts,
or the final add action. At most two primary actions and two app shortcuts are
directly visible; an add action reserves one app slot. Other visible actions stay
in the existing accessible DropdownMenu. Hidden actions are excluded from both
surfaces. These display roles grant no permissions or installation rights.

The shell reserves body space and publishes `--workbench-bottom-inset` for existing
floating controls, the board work dock, and minimap. Desktop uses zero inset.
Opening a mobile context panel transfers focus into it; explicit dismissal returns
focus to its launching rail button or overflow trigger. Nested menus retain Escape
ownership. Immersive chat retains its existing visual viewport handling.

This is the shell portion of the mobile app navigation work. The general-user
catalog routes, personal pin persistence, installation/binding consent flows, and
authenticated canary acceptance remain separate requirements. Source browser
fixtures do not establish canary acceptance or a measured performance improvement.

Run the focused shell checks from `src/ui`:

```sh
npm run typecheck
npm run test:workbench-shell
```

The initial complete run passed 34 of 35 cases. The description navigator Home-key
case retained `scrollTop=2` instead of zero; that failure is preserved and is not
waived. The final selected rail, focus, floating-dock, and shell cases passed 12
tests; three viewport cases were rerun after the final border inset adjustment.

The Home-key failure was subsequently reproduced in four of five isolated runs.
Keyboard scrolling now reapplies its target on the next render frame, matching
the existing section-navigation correction. The previous scheduled navigation is
cancelled before every new request. The same Home-key check then passed five of
five runs, and the complete Workbench suite passed all 35 cases in 30.2 seconds.
Type checking and scoped ESLint also passed. Disabling browser scroll anchoring
and using native instant scrolling alone did not reliably fix the failure; those
experiments were discarded. Exact browser-internal causality remains unproven.
These results still do not establish authenticated canary acceptance.
