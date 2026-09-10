# HDF Vision UI redesign roadmap

This checklist tracks the staged visual redesign based on the approved dark
industrial mockups. Functional vision, recipe, camera and PLC behaviour stays
outside the scope unless a stage explicitly says otherwise.

## Stage 1 — Shared visual system

- [x] Define shared colors, typography, spacing, radii and borders.
- [x] Unify buttons, inputs, panels, tables, scrollbars and status badges.
- [x] Provide reusable semantic roles for primary, destructive, OK, warning
      and NOK states.
- [x] Apply the shared visual system to the main application shell.

## Stage 2 — RUN workspace

- [x] Recompose RUN into a clear operator-focused layout.
- [x] Keep the live inspection image as the dominant element.
- [x] Add compact camera/view information and operational controls.
- [x] Add clear overall OK/NOK status, cycle time and latest tool result cards.
- [x] Preserve all existing trigger, live-view, recipe, camera and PLC wiring.

## Stage 3 — Collapsible inspection filmstrip

- [ ] Show a bounded list of recent inspection thumbnails.
- [ ] Display status, timestamp and cycle time per inspection.
- [ ] Open the selected inspection detail from a thumbnail.
- [ ] Allow the operator to collapse/expand the strip.
- [ ] Persist the collapsed state without retaining unbounded image data.

## Stage 4 — Results and diagnostics

- [x] Add a simple operator-facing results browser.
- [x] Show the current inspected image with selectable ROI and NOK localization overlays.
- [x] Keep advanced metrics behind an optional detail area.
- [x] Support practical date/status filters and CSV export.
- [ ] Verify Results page on the operator display with production history.

## Stage 5 — SETUP navigation and properties

- [x] Unify SETUP navigation with the same visual system.
- [x] Reorganize recipe, communication and camera entry points.
- [x] Keep the Golden Wizard as the primary tool/pipeline editor.
- [ ] Standardize property sections and validation feedback.
  - [x] Shared parameter/threshold/advanced form spacing and wrapping.
  - [x] Display declared units consistently in controls and range tooltips.
  - [ ] Review missing units and cross-field validation for each tool.

### Locator follow-up

- [ ] Overiť režim zarovnania podľa referenčnej hrany A–B na reálnych snímkach.
- [x] Vyhodnotiť kvalitu šablóny a upozorniť na nejednoznačné zhody.
- [x] Pri template matchingu presne rešpektovať otočený tvar search/template oblasti,
      nie iba jej osovo zarovnaný bounding box.
- [ ] Overiť nové maskované hľadanie, varovania a čas cyklu na produkčných snímkach.

## Stage 6 — Resolution and operator polish

- [x] Pico: scrollable content with persistent Save/Cancel footer.
- [x] Golden: wrap top/footer actions, maximize instead of forced fullscreen,
      and provide workspace scrolling when its controls exceed the window.
- [x] Results: wrap filters onto additional rows.
- [ ] Verify Golden's complete layout with recipes and physical display scaling.

- [ ] Verify layouts at the target Jetson display resolutions.
- [ ] Check long Slovak labels, scaling and touch-friendly hit targets.
- [ ] Reduce visual noise and confirm status readability at distance.
- [ ] Profile thumbnail/image handling and remove avoidable UI allocations.
  - [x] Coalesce pending Results image requests to the newest selection.
  - [ ] Measure memory and latency on the operator display; verify Results filters,
        paging, missing images, overlays and CSV export interactively.
