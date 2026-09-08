## 2023-10-27 - Custom Interactive Elements Need Keyboard Support
**Learning:** In the `Table.tsx` component, sortable table headers and clickable table rows were implemented using `onClick` events without equivalent `onKeyDown` events or explicit `tabIndex`/`role` attributes. This made them completely inaccessible to keyboard-only and screen reader users. Simply adding visual hover states is not enough for custom interactive components.
**Action:** When creating custom interactive components (like clickable `div` or `span` elements instead of native `<button>`s), always pair `onClick` with `onKeyDown` (handling at minimum "Enter" and " "), set `tabIndex={0}`, set the appropriate ARIA role (e.g., `role="button"`), and ensure explicit `focus-visible` styles are implemented to make focus states clearly visible for keyboard navigation.

## 2024-07-13 - Interactive Custom Components Need Full Keyboard Support
**Learning:** When using custom components (like `div` elements) as interactive buttons (e.g., clickable plugin rows), `onClick` alone is insufficient for keyboard and screen-reader users.
**Action:** Pair `onClick` with `onKeyDown` (Enter/Space), `tabIndex={0}`, `role="button"`, and explicit `focus-visible` styles (e.g. `.ct-plugin:focus-visible`).
