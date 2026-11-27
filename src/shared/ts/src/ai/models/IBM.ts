export const IBM_MODELS = [
    "ibm/granite-3-2b-instruct",
    "ibm/granite-3-8b-instruct",
    "ibm/granite-13b-instruct-v2",
    "sentence-transformers/all-minilm-l12-v2",
    "ibm/slate-125m-english-rtrvr-v2",
    "ibm/slate-30m-english-rtrvr-v2",
    "intfloat/multilingual-e5-large",
] as const;

export const IBM_WATSONX_URLS = [
    "https://us-south.ml.cloud.ibm.com",
    "https://eu-de.ml.cloud.ibm.com",
    "https://eu-gb.ml.cloud.ibm.com",
    "https://au-syd.ml.cloud.ibm.com",
    "https://jp-tok.ml.cloud.ibm.com",
    "https://ca-tor.ml.cloud.ibm.com",
] as const;

export type TIBMModelName = (typeof IBM_MODELS)[number];
export type TIBMWatsonXURL = (typeof IBM_WATSONX_URLS)[number];
