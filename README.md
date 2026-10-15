# Invoice extraction and reliability

**30 minutes coding, a 5 minute Docker step, then a 15 minute walkthrough.** Build a small Python function that uses the supplied model helper to extract fields from invoice text, checks the result against the invoice, and decides whether it is ready or needs human review.

All five invoice texts in `fixtures/` are synthetic. They use different layouts and labels, which is why a language model is used to read them. No PDF parsing, OCR, UI, deployment, tax arithmetic or accounting expertise is required.

## Your task

1. Implement `process_invoice(invoice_text)` method in `solution.py`. 
2. Write a prompt, call `ask_model(prompt)`, parse the response, validate it against the rules below, and return a Python dictionary. 

** The helper returns model text; it does not promise valid JSON. Handle model failures and malformed responses without crashing the batch.

Keep the supplied helper and runner usable. Add **three meaningful deterministic tests** in the supplied test scaffold: one successful extraction and one uncertainty or failure case. Assert returned values, status and issues. Patch `solution.ask_model` to return controlled text or raise an exception; tests must not require a live API call. The starter's two skipped TODO tests are placeholders, not completed tests.

AI tools and documentation are allowed. You must explain the code and adapt it during the walkthrough. Pydantic is allowed but not required; ordinary Python is acceptable. Library choice is not scored.

## Result contract

Return exactly these top-level keys:

| Key | Value |
| --- | --- |
| `supplier` | Nonempty string or `None` |
| `invoice_number` | Nonempty string or `None` |
| `date` | Valid calendar date string in `YYYY-MM-DD`, or `None` |
| `currency` | Three-letter uppercase currency code written in the invoice, or `None` |
| `total` | Python `float` rounded to 2 decimal places, finite and nonnegative, or `None` |
| `status` | `"ready"` or `"needs_review"` |
| `issues` | List of specific, human-readable strings explaining each review reason |

Python `None` becomes JSON `null` in runner output. `ready` requires all five fields to be present, valid and supported by the invoice text, with `issues == []`. Otherwise use `needs_review` and at least one specific reason.

## Validation rules

**Every returned value must be supported by the invoice text.** Do not trust a model value just because it is well formed.

- **Supplier** is the business issuing the invoice, not the customer being billed. Strip outer whitespace and preserve spelling, case and punctuation.
- **Invoice number:** preserve spelling, case, punctuation and leading zeros exactly. Trimming outer whitespace is the only change allowed.
- **Text values must appear in the invoice.** After trimming, the returned `supplier`, `invoice_number`, `date` and `currency` must each appear in the invoice text exactly as returned (case-sensitive).
- **Date:** dates in these invoices are written as `YYYY-MM-DD`. The returned date must use that format and be a real calendar date. Do not repair invalid dates or convert other formats.
- **Currency** must be an explicit three-letter uppercase code written in the invoice. A symbol such as `$`, a city, a province or a country does not establish a currency.
- **Total** is the final amount due, not a subtotal, tax or line item. Source amounts use simple decimal notation with no thousands grouping; a preceding `$` may be removed to read the amount, but does not establish currency.
  - The model may give the total as a number or a numeric string. Either way it must equal, by decimal value, an amount written in the invoice. `125.5` and `125.50` agree.
  - Reject booleans, negatives, `NaN` and infinity.
  - Return it as a `float` rounded to 2 decimal places, for example `125.5` for `125.50` (Python and JSON do not keep the trailing zero). Do not return `Decimal` or `str`.
- **Missing, conflicting or unreadable values:** any field that is missing, conflicting, unreadable, invalid or not found in the invoice becomes `None`, with a specific issue. Keep other independently trustworthy fields.
- **Conflicts:** if the invoice gives two different values for the same field, return `None` and do not choose one. Checking that a value appears in the text will not catch this, so your prompt should get the model to report missing, unreadable or conflicting values. You may ask the model for extra information (for example, a reason for each empty field). Your function still returns only the seven keys above.
- **Model failure:** if the model call fails or its response cannot be parsed into a usable object, return all five fields as `None`, `needs_review`, and a specific model or response failure issue. A parseable object with individual missing or invalid fields can retain other valid fields.

Aim for a clear solution for these invoices. You do not need a general invoice parser, and writing one from regular expressions is not the point of this exercise. Prompt quality alone does not replace validation.

## Setup

The interviewer will supply a temporary test-project API key and model name separately. Do not put credentials in code, or in version control.

```sh
cp .env.example .env
```

Set `OPENAI_API_KEY` and `OPENAI_MODEL` in `.env` using the supplied values. Write the values without quotes. The `.env` file is ignored by Git and excluded from the Docker image.

### Option A: local Python (3.10 or newer)

```sh
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
python run.py
python -m unittest discover -s tests -v
```

The helper loads `.env` when called; existing environment variables take precedence.

### Option B: Docker

Requires Docker Desktop (running) or Docker Engine. No local Python is needed. Run every command from the project root folder.

```sh
# 1. Build the image: installs Python 3.12 and the requirements, and copies in your code.
#    Rebuild whenever you want to check a fresh copy of your code.
#    .env is never copied into the image (see .dockerignore).
docker build -t invoice-test .

# 2. Run your unit tests inside the container. No API key is needed:
#    tests patch solution.ask_model instead of calling the model.
docker run --rm invoice-test python -m unittest discover -s tests -v

# 3. Process the five fixtures with the real model.
#    --env-file passes OPENAI_API_KEY and OPENAI_MODEL in at run time,
#    so the key never becomes part of the image.
docker run --rm --env-file .env invoice-test
```

While you are coding, skip rebuilding by mounting your working folder over the copy in the image. The container then runs whatever is currently on disk:

```sh
# Run your tests against the files you are editing.
docker run --rm -v "$PWD":/app invoice-test python -m unittest discover -s tests -v

# Run the batch against the files you are editing.
docker run --rm --env-file .env -v "$PWD":/app invoice-test
```

Notes:

- `--rm` deletes the container when it finishes, so runs don't pile up.
- Write the values in `.env` without quotes. Docker's `--env-file` keeps quotes as part of the value, which breaks the key.
- The batch exits with code 1 if any invoice crashed instead of returning a result.
- On Windows PowerShell, use `${PWD}` instead of `"$PWD"`.

The first `run` processes the five fixtures; the second runs your tests without an API key. While you are coding, you can skip rebuilding by mounting your working folder:

`python run.py` processes the five fixtures. The initial implementation is deliberately unfinished; its output does not represent a working extraction solution. Finish `solution.py` and replace the skipped test placeholders.


## Walkthrough

Be ready to:

- run one success and one bad case;
- explain your tests and review decisions;
- adapt your code to one new invoice in a layout you have not seen;
- describe how you would evaluate the real model before using it with customer invoices.
