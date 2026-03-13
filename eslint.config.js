import nextConfig from "eslint-config-next";
import nextTypescript from "eslint-config-next/typescript";

export default [
  { ignores: ["src/components/ui/**"] },
  ...nextConfig,
  ...nextTypescript,
];
