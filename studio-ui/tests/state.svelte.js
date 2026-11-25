// Deeply reactive test data, like the app's project state (so bind: in components is reactive).
export function reactive(value) {
  const state = $state(value);
  return state;
}
