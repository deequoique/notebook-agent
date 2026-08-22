export const apiOrigins = Object.freeze({
  production: "https://notebookai.deequoique.tech",
  local: "http://127.0.0.1:8000",
});

export function buildTarget(value = "production") {
  if (!Object.hasOwn(apiOrigins, value)) {
    throw new Error(`unsupported extension build target: ${value}`);
  }
  return value;
}

export function apiHostPermission(target) {
  return `${apiOrigins[buildTarget(target)]}/*`;
}

export const allApiHostPermissions = Object.freeze(
  Object.keys(apiOrigins).map((target) => apiHostPermission(target)),
);
