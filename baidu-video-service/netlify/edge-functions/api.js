import { createApi } from "../../src/api.mjs";

const handler = createApi({ env: name => Netlify.env.get(name) });
export default handler;

export const config = { path: ["/healthz", "/v1/*"], onError: "fail" };
