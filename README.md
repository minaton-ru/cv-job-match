# CV Job Match

Searching for and parsing job openings on company websites, and preparing a resume and cover
letter for a selected vacancy. Domain terms are defined in [GLOSSARY.md](GLOSSARY.md), and design
decisions are recorded in [docs/adr/](docs/adr/).

## Install

The project uses `uv` and Python 3.13.

```sh
uv sync
```

## Configure

Settings come from environment variables prefixed with `CV_JOB_MATCH_`, or from a `.env` file in
the working directory. Never commit `.env`.

| Variable                    | Default        | Meaning                                                   |
| --------------------------- | -------------- | --------------------------------------------------------- |
| `CV_JOB_MATCH_SOURCES_PATH` | `sources.yaml` | Path to the Sources config.                               |
| `CV_JOB_MATCH_PROFILE_PATH` | `profile`      | Path to the Candidate Profile folder.                     |
| `CV_JOB_MATCH_LOG_LEVEL`    | `DEBUG`        | Lowest level logged: DEBUG, INFO, WARNING, ERROR or CRITICAL. |
| `CV_JOB_MATCH_EXTRA_CA_PATH` | not set       | PEM file with an extra root certificate, trusted only for the hosts of Sources with `extra_ca: true`. |
| `CV_JOB_MATCH_LLM_BASE_URL` | not set        | Base URL of the OpenAI-compatible AI provider; not set means the OpenAI API. |
| `CV_JOB_MATCH_LLM_MODEL`    | not set        | Chat model for Assessments and Tailoring; it must support tool calling. |
| `CV_JOB_MATCH_LLM_API_KEY`  | not set        | API key of the AI provider.                               |
| `CV_JOB_MATCH_TELEGRAM_BOT_TOKEN` | not set   | Token of the Telegram bot.                                |
| `CV_JOB_MATCH_TELEGRAM_CHAT_ID` | not set    | The only chat the bot answers; not set means it answers none. |
| `CV_JOB_MATCH_DATABASE_URL` | not set        | PostgreSQL URL with the asyncpg driver, such as `postgresql+asyncpg://user:password@host:5432/name`. |

### Sources config

The Sources config is a YAML file that lists each Source to monitor. It is excluded from Git
(see `.gitignore`), because it names the Companies being watched. It is validated at startup,
and an invalid file stops the app with an error that names the Source and the problem.

```yaml
defaults:
  interval: 1d            # how often each Source is checked; default 1d

sources:
  - id: example-backend   # unique Source id
    company: Example Co   # several Sources may share a Company
    technique: json_endpoint   # json_endpoint, html_css or embedded_state
    enabled: true         # optional, default true; false switches the Source off
    interval: 12h         # optional override of defaults.interval
    notes: Backend roles  # optional free text
    list:                 # every other key is a technique option,
      url: https://careers.example.com/api/jobs   # read by the technique adapter
```

Intervals are a whole number followed by `s`, `m`, `h` or `d`, such as `30m`, `12h` or `1d`.

A Source with `extra_ca: true` is fetched with the default CA bundle plus the root certificate
from `CV_JOB_MATCH_EXTRA_CA_PATH`. Certificate checks are never turned off. If the variable is
not set, a warning is logged and those hosts use the default bundle only.

### `json_endpoint` options

Requests are GET by default. With `method: POST`, an optional `body` is sent as JSON; a `body`
with GET is rejected. Paths are dotted, such as `data.items` or `tags.0`.

```yaml
    list:
      url: https://careers.example.com/api/jobs   # required
      method: POST                  # optional; GET (default) or POST
      params: {q: python}           # optional query parameters
      body: {query: {text: python}} # optional, POST only; sent as JSON
      items_path: data.items        # required; '' when the response is the list itself
      id_field: id                  # required; path to the site's own ID inside an item
      fields:                       # optional; field name -> path inside an item
        title: name
        url: link
      pagination:                   # optional; without it, one page is read
        style: page                 # page (page number) or offset (index of the first item)
        param: page                 # query parameter for GET, top-level body key for POST
        start: 1                    # optional; first value, default 1 for page and 0 for offset
        size: 20                    # items per full page; required for offset
        total_path: meta.total      # optional; path to the total number of items
        max_pages: 100              # optional; default 100
    detail:                         # optional; without it, no description is fetched
      url: https://careers.example.com/api/jobs/{id}   # {id} is the Vacancy's ID
      method: GET                   # optional; GET (default) or POST
      params: {lang: en}            # optional; {id} is replaced in values too
      body: {job: {ref: '{id}'}}    # optional, POST only; {id} is replaced in every text
      description_fields: [body.summary, body.duties]  # joined by blank lines
```

`{id}` must appear in the detail `url`, or in a value of `params` or `body`, so a fixed
description endpoint such as `https://careers.example.com/api/job-details` works with the ID in
its body.

Pages are read one after another, and reading stops after the first page that is empty, holds
fewer than `size` items, or brings the count up to the number at `total_path`. The pagination
`param` must not also be set in `params` (GET) or `body` (POST). Invalid options or combinations
are reported when the Source is checked, with the Source and the problem named.

A failure on any page (network error, non-2xx status, malformed JSON, a missing items path, an
item without an ID, or a `total_path` that does not hold a number) makes the whole list fetch
fail, so no partial list is used and the run is reported as suspect. So does a list with more
than `max_pages` pages; after `max_pages` full pages, one more page is fetched to check that it
is empty. Without `size` and `total_path`, only an empty page ends the list.

In every technique, a field mapped as `description` is the Vacancy's full text, so its
description is not fetched from `detail`.

In every technique, field values and IDs are stripped of surrounding whitespace, and a missing,
empty or whitespace-only field value is read as no value. An item whose ID is missing or blank
makes the list fetch fail.

### `html_css` options

For Sources whose list is server-side HTML. Values are read with **CSS selectors**; selector syntax
is checked when the Source is checked. Requests are GET, and one page is read.

```yaml
    list:
      url: https://careers.example.com/jobs   # required
      params: {q: python}                     # optional query parameters
      list_selector: ul.jobs                  # required; the element that holds the list
      item_selector: li.job                   # required; each item inside that element
      id_field:                               # required; where the site's own ID is
        selector: a                           # the element inside the item
        attribute: href                       # optional; read this attribute, not the text
        pattern: '/jobs/(\d+)/'               # optional; keep only this part of the value
      fields:                                 # optional; field name -> where its value is
        title: h3                             # short form: only a selector, reads the text
        url: {selector: a, attribute: href}   # same keys as id_field
    detail:                                   # optional; without it, no description is fetched
      url: https://careers.example.com/jobs/{id}   # {id} is the Vacancy's ID
      params: {lang: en}                      # optional; {id} is replaced in values too
      description_selectors: [div.summary, div.duties]  # every match, joined by blank lines
```

`id_field` and each entry of `fields` say where one value is, with the same three keys:

| Key         | Meaning                                                                        |
| ----------- | ------------------------------------------------------------------------------ |
| `selector`  | CSS selector inside the item; the first match is used. Empty or missing means the item itself. |
| `attribute` | Attribute to read. Without it, the element's text is read, with its inner whitespace collapsed. |
| `pattern`   | Regular expression with at least one capture group. The first group becomes the value. |

Plain text instead of the keys is a `selector`, so `title: h3` is the same as
`title: {selector: h3}`.

Use `pattern` when the value holds more than is needed, most often when the site's own ID is only
inside a link. In the example above, the link `/en/jobs/42/` gives the ID `42`, and the detail
page is requested at `https://careers.example.com/jobs/42`. A value the pattern does not match is
read as no value, which for `id_field` makes the list fetch fail.

A page where `list_selector` matches nothing, including a malformed or non-HTML page, makes the
list fetch fail; a list element with no items is an empty list.

### `embedded_state` options

For Sources whose page embeds its framework state in a script element. The state is read as
JSON, and items, IDs and fields are found with dotted paths as in `json_endpoint`. Requests are
GET, and one page is read.

```yaml
    list:
      url: https://careers.example.com/jobs   # required
      params: {q: python}                     # optional query parameters
      script_selector: script#app-state       # required; the script element with the state
      assignment: window.__STATE__            # optional; for `window.__STATE__ = {...};`
      items_path: props.jobs                  # required; '' when the state is the list itself
      id_field: id                            # required; path to the site's own ID in an item
      fields: {title: name, url: link}        # optional; field name -> path inside an item
    detail:                                   # optional; without it, no description is fetched
      url: https://careers.example.com/jobs/{id}   # {id} is the Vacancy's ID
      params: {lang: en}                      # optional; {id} is replaced in values too
      script_selector: script#app-state       # required
      assignment: window.__STATE__            # optional
      description_fields: [job.summary, job.duties]  # joined by blank lines
```

Without `assignment`, the whole script must be JSON. With it, the JSON value right after
`<assignment> =` is read, and any code after it is ignored. A missing script, a missing
assignment, malformed JSON or a missing items path makes the list fetch fail.

### Candidate Profile

The Profile folder holds the Candidate's master resume and criteria. It is excluded from Git
(see `.gitignore`), because it holds personal data. It is loaded and validated at startup: a
missing folder or file, a file that is not UTF-8, an empty resume, malformed YAML or an invalid
field stops the app with a logged error that names the problem.

```text
profile/
  resume.md       # master resume, Markdown; must not be empty
  criteria.yaml   # the Candidate's criteria
```

```yaml
language: en                  # required; language of Application Packs
score_threshold: 60           # optional, 0-100, default 60; lowest score that is sent
preferences: [Remote work]    # optional lists of text, default empty
excluded_roles: [Sales Manager]
strengths: [Python]
weaknesses: [Frontend]
prefilter:                    # optional; Pre-filter keyword rules, an empty list switches one off
  title_keywords: [python, backend]   # the title must contain at least one
  excluded_title_keywords: [director] # a title containing any is filtered out
  cities: [Example City]              # the city must contain one of them
```

Unknown keys are rejected, so a misspelled key is reported instead of ignored. List items must
not be blank.

The Profile version is a SHA-256 hash of both files' text, so the same content always gives the
same version and any edit, comments included, gives a new one.
`App.profile.save_snapshot_if_new()` saves the loaded Profile through `ProfileRepository` only
when its version is new. `serve` calls it at startup.

## Run

```sh
uv run cv-job-match check-config                  # validate the config and list the Sources
uv run cv-job-match --sources other.yaml check-config
uv run cv-job-match check-source example-backend  # dry-run one Source, saving nothing
uv run cv-job-match serve                         # run the service (see Deploy)
```

Every command exits with code 3 when the Sources config or the Profile is invalid and prints
the error.

`check-source <source id>` fetches the Source's list and the descriptions of the first three
Vacancies, then prints how many Vacancies it found, a preview of those three, and the suspect
reason when the list fetch failed on any page (see the technique options above). Nothing
is saved. Switched-off Sources can be checked too. It exits with:

| Code | Meaning                                                        |
| ---- | -------------------------------------------------------------- |
| 0    | The list was read                                              |
| 1    | The run is suspect; the reason is printed                      |
| 2    | Unknown Source id, or its technique has no adapter yet         |
| 3    | Invalid Sources config or technique options, or a bad extra CA |

### Discovery

`Discovery.run_due()` is the daily Discovery tick. It checks every enabled Source that never ran,
or whose interval has passed since its last Scrape Run, failed runs included. Sources of one
Company run one after another, and different Companies run concurrently. Each Scrape Run is
saved with all its changes at once, and logged with its status and counts.

- The first successful Scrape Run of a Source is its Baseline Run: every Vacancy it finds is
  recorded as already seen, and no descriptions are fetched.
- Later runs record new Vacancies. A new Vacancy's description is fetched once, unless its list
  item already has a `description` field. A new Vacancy whose description fetch fails is left
  out, and the next run finds it again.
- A Vacancy listed twice in one list, or by two Sources of one Company, is recorded once.
- A changed value of a known Vacancy's list field is recorded as a Revision. Only the fields the
  Source maps are compared, and a Revision never fetches the description again or triggers an
  Assessment.
- A Vacancy is closed when no Source of its Company lists it any more, judged by each Source's
  latest successful Scrape Run. It is kept, not deleted, and if it is listed again it is reopened
  as the same Vacancy; reopening changes nothing else about it.
- A run is suspect when the list fetch failed on any page, or when it found nothing while the
  last successful run of that Source found Vacancies. Finding nothing after a successful run that
  also found nothing is not suspect. A suspect run records no changes and closes nothing, and it
  is in the `DiscoveryReport` with its reason.
- A failed list fetch, or invalid technique options, is recorded as a suspect run and never
  raises. A repository error does raise.

`run_due` needs a Discovery repository, which is wired once `CV_JOB_MATCH_DATABASE_URL` is set.

Requests go through `PoliteHttpClient`: at most one request per second per host, with a
browser-like User-Agent. Redirects are not followed; a redirected list shows up as an
`HTTP 3xx` suspect reason, so point `url` at the final address.

### Evaluation

`App.run_tick()` is the daily pipeline: it runs `Discovery.run_due()`, sends the alerts
described below, then `Evaluation.evaluate_waiting()`, which pre-filters and assesses every
saved Vacancy that is waiting: not a Baseline Vacancy, not a Closed Vacancy, not yet assessed,
not filtered out and not shortlisted, applied or dismissed.
A Closed Vacancy that is reopened before it is assessed waits again. Waiting Vacancies are
read from the repository, so a Vacancy saved before a crash is assessed on the next tick. `evaluate_vacancy(key)` assesses one
Vacancy, identified by its Company and site ID, and does nothing when it is not waiting.

Before any AI call, the Pre-filter checks the Vacancy's `title` and `city` list fields against
the Profile's `prefilter` rules. Keywords and cities match as case-insensitive whole words, so
`go` does not match "Google" and a plural such as `directors` needs a keyword of its own. Map
the Source's city as a `city` field to use the `cities` rule. A missing, empty or
whitespace-only title or city is not a mismatch and never filters a Vacancy out. A rejected
Vacancy makes no AI call and sends no message; it is saved as filtered out with the reason and
the Profile version, and it no longer waits. The Vacancy's language is not checked: set up each
Source so that it lists only Vacancies in the Profile's `language` (see
[ADR 0005](docs/adr/0005-no-language-check-in-prefilter.md)).

An Assessment is made by the chat model with structured output (tool calling): a score from 0
to 100, a verdict (apply, maybe or skip), the matching strengths, the gaps and the exclusions
triggered. It is saved with the Profile version it was made against. A failed AI call or invalid
structured output is logged, nothing is saved, and the Vacancy keeps waiting for the next tick.

When the score is at or above the Profile's `score_threshold`, the Candidate gets one message
through the `Notifier` with the Company, title, link, score, verdict, strengths, gaps and
exclusions, and buttons to shortlist, mark as applied and dismiss it (see Decisions). The
Assessment is saved before the message is sent, so a failed send is logged as an error, is not
retried, and does not stop the tick.

The chat model is created from the `CV_JOB_MATCH_LLM_*` settings through any OpenAI-compatible
API. Without a model or API key, `App.llm` is `None`. Evaluation needs a repository, a chat model
and a notifier, and raises without any of them.

### Alerts

For every Suspect Scrape Run of the tick, the Candidate gets one message through the `Notifier`
naming the Source, its Company and the reason, such as
`Suspect Scrape Run of Source example-backend (Example Corp): request failed: connection refused`.
A failed send is logged as an error, is not retried, and does not stop the alerts for other
Sources or Evaluation. Without a notifier the tick raises before Discovery starts.

### Decisions

The Candidate acts on Vacancies from the chat. `App.bot` holds thin handlers that do not depend
on a chat library: `handle_button(chat_id, decision, key)` for a Vacancy button and
`handle_list_command(chat_id)` for `/list`. Each returns the reply text, or `None` for an update
from any chat other than `CV_JOB_MATCH_TELEGRAM_CHAT_ID`, which is ignored and logged.

Buttons delegate to `App.decisions` (`shortlist_vacancy`, `mark_applied`, `dismiss_vacancy`).
Only these status changes are allowed: new to shortlisted, applied or dismissed, and shortlisted
to applied or dismissed. Shortlisting a Shortlisted Vacancy again is allowed, keeps its status and
retries Tailoring. Anything else, such as applied to shortlisted, is refused and logged,
leaves the status unchanged, and the reply says why. A button for an unknown Vacancy replies that
it is not known.

A Vacancy with a decision no longer waits for Assessment, and an Assessed one never waits
again, so a Dismissed or Applied Vacancy sends no message after a Revision or reopening.

`/list` shows the ten most recently Assessed Vacancies, below the threshold included, each with
its score, verdict and status, or says there are none yet.

Decisions need a Decision repository. In Telegram, the handlers are wired to `/list` and to the
Vacancy buttons by `create_dispatcher` in `src/cv_job_match/bot/telegram.py`. Telegram allows only
64 bytes of button data, so a button names the Vacancy's Company by the id of the Company's first
Source in the config. A button whose Source has since been removed from the config replies that
it is no longer known.

### Tailoring

Every Shortlist button press that is accepted starts Tailoring of the Vacancy as a background
task, `App.tailoring.start_tailoring(key)`, and the reply is sent right away. A press while that
Vacancy's Tailoring is still running starts no second one.
`tailor_application(key)` then:

1. Fetches the Vacancy's description again from the Source that recorded it (see
   [ADR 0004](docs/adr/0004-fetch-description-once-refresh-before-tailoring.md)). When that
   Source has no `detail` options, the description saved at discovery is used.
2. Asks the chat model, with structured output, for the tailored resume in Markdown and the cover
   letter, in the Profile's `language`. The prompt allows it only to select, reorder and reword
   facts from the master resume, never to add new ones.
3. Renders the resume PDF from a fixed template: A4 paper and the CSS in `constants.py`.

The Candidate gets the Application Pack through the `Notifier`: `resume.pdf` and `resume.md` as
files, then the cover letter as a plain text message. Its metadata (the Vacancy, the Profile
version, the language and the time) is saved through the Tailoring repository. When the
description cannot be fetched again or the AI call fails, the Candidate gets a message such as
`Tailoring of Vacancy v1 of Example Co failed: the AI call failed. Press Shortlist to try again.`,
nothing is saved, and pressing Shortlist again retries. There is no second AI verification pass.
Any other error, such as a failed send or a missing repository, chat model or notifier, is
logged and stops that Tailoring.

Tailoring needs a Tailoring repository. `App.tailoring.wait_for_background_tasks()` waits for
every started Tailoring.

### Storage

Everything is stored in PostgreSQL through `PostgresRepository` (Discovery, Evaluation, Decision
and Tailoring) and `PostgresProfileRepository` in `src/cv_job_match/db/postgres.py`, which share one
engine from `create_database_engine(url)`. The schema holds Companies and Sources (mirrored from
the config by name and ID on every saved Scrape Run), Vacancies (one per Company and site ID),
Scrape Runs with the IDs each one listed, Revisions, Profile snapshots, Assessments, Pre-filter
rejections and Application Pack metadata.

A Scrape Run is saved in one transaction: when any change fails, such as a Revision of an unknown
Vacancy (`VacancyNotFoundError`), nothing of the run is saved. Saving an outcome, a status or an
Application Pack for an unknown Vacancy raises `VacancyNotFoundError` too.

Migrations are Alembic scripts in `src/cv_job_match/db/migrations/`. To create or update the schema:

```sh
CV_JOB_MATCH_DATABASE_URL=postgresql+asyncpg://user:password@localhost:5432/cv_job_match \
    uv run alembic upgrade head
```

`migrate_database(url)` does the same from code, and `serve` runs it at startup. `build_app`
wires the PostgreSQL repositories once `CV_JOB_MATCH_DATABASE_URL` is set.

### Logs

Every log of the service, from `CV_JOB_MATCH_LOG_LEVEL` up, goes to `cv_job_match.log` in the working directory.
The file is rotated at 10 MB, and the three previous files are kept as `cv_job_match.log.1` to
`.3`. Log files are excluded from Git. `serve` also writes every log to stderr, so Scrape Runs,
Evaluation and Tailoring show up in the container output (`docker compose logs app`).

## Deploy

The service runs with Docker Compose: one app container, which runs `cv-job-match serve`, and
PostgreSQL 18. `serve` checks the secrets, migrates the database, saves the Profile snapshot,
and then runs the Telegram bot and the scheduler in one asyncio event loop. The scheduler runs the
daily pipeline (`App.run_tick`) once an hour; each Source is still checked only when its interval
has passed, and a failed tick is logged and retried an hour later.

1. Put the Sources config at `sources.yaml` and the Profile at `profile/` next to
   `compose.yaml`. Both are mounted read-only into the app container and never built into the
   image.
2. Create `.env` next to `compose.yaml` with the variables below, and never commit it.
3. Start the service:

   ```sh
   docker compose up -d --build
   docker compose logs -f app
   ```

| Variable                         | Needed by | Meaning                                               |
| -------------------------------- | --------- | ----------------------------------------------------- |
| `POSTGRES_USER`                  | Compose   | Database user; required.                              |
| `POSTGRES_PASSWORD`              | Compose   | Database password; required. Use only URL-safe characters, as it goes into the database URL. |
| `POSTGRES_DB`                    | Compose   | Database name; optional, default `cv_job_match`.      |
| `CV_JOB_MATCH_TELEGRAM_BOT_TOKEN` | `serve`  | Token of the Telegram bot.                            |
| `CV_JOB_MATCH_TELEGRAM_CHAT_ID`  | `serve`   | The Candidate's chat.                                 |
| `CV_JOB_MATCH_LLM_MODEL`         | `serve`   | Chat model for Assessments and Tailoring.             |
| `CV_JOB_MATCH_LLM_API_KEY`       | `serve`   | API key of the AI provider.                           |
| `CV_JOB_MATCH_LLM_BASE_URL`      | optional  | See Configure.                                        |
| `CV_JOB_MATCH_LOG_LEVEL`         | optional  | See Configure.                                        |

Compose builds `CV_JOB_MATCH_DATABASE_URL` from the `POSTGRES_*` variables, so do not set it in
`.env` for Compose. When a Source uses `extra_ca: true`, mount the certificate file into the app
container, for example with a `compose.override.yaml`, and point `CV_JOB_MATCH_EXTRA_CA_PATH` at
its path inside the container.

A missing, empty or whitespace-only `serve` variable stops `serve` with exit code 3 and an error
that names every missing variable, and so does a bot token that Telegram rejects. A malformed bot
token stops every command with exit code 3. Compose itself
refuses to start without `POSTGRES_USER` or `POSTGRES_PASSWORD`.

To update, pull the new code and rebuild; migrations run when the app starts:

```sh
git pull
docker compose up -d --build
```

Editing `sources.yaml` or `profile/` needs only `docker compose restart app`. The database lives
in the `db-data` volume, which survives rebuilds; `docker compose down -v` deletes it.

## Develop

```sh
uv run ruff format .
uv run ruff check --fix .
uv run pytest -q
```

Everything is wired by one factory, `build_app(settings, *, repos, llm, http, notifier, clock)`
in `src/cv_job_match/app.py`. Production passes only settings, and tests pass Fake classes for
any dependency, such as `FakeHttpClient` in `tests/fakes.py` with canned responses per URL,
`Repositories(discovery=FakeDiscoveryRepository(), profile=FakeProfileRepository())` for
in-memory stores, `FakeNotifier` for sent messages, their buttons and sent files, or
`FakeChatModel`, built by `build_fake_chat_model` with answers from `build_assessment_message` or
`build_tailoring_message`, or `FakeTelegramBot` for `TelegramNotifier`. `FakeDiscoveryRepository` also serves as the `evaluation`, `decision`
and `tailoring` repositories, so all of them share one store. An autouse fixture in `tests/conftest.py` runs each test in its own temporary
directory with a synthetic `profile/` folder.

`tests/test_repository_contract.py` runs one contract suite against both the in-memory fakes and
PostgreSQL, so the fakes keep behaving like the database. Its `postgres` tests are skipped unless
`CV_JOB_MATCH_TEST_DATABASE_URL` is set. They drop and migrate the schema of that database, so
point it at a throwaway one, for example:

```sh
docker run -d --rm --name cv-job-match-test-db -e POSTGRES_PASSWORD=test \
    -e POSTGRES_DB=cv_job_match_test -p 55432:5432 postgres:18-alpine
CV_JOB_MATCH_TEST_DATABASE_URL=postgresql+asyncpg://postgres:test@localhost:55432/cv_job_match_test \
    uv run pytest -q
uv run pytest -q -m postgres   # only the PostgreSQL contract tests
```

| Path                                 | Holds                                             |
| ------------------------------------ | ------------------------------------------------- |
| `src/cv_job_match/app.py`            | `App` and the `build_app` factory                 |
| `src/cv_job_match/settings.py`       | Settings read from the environment                |
| `src/cv_job_match/sources.py`        | Sources config schema and loader                  |
| `src/cv_job_match/discovery.py`      | `Discovery` (`run_due`, `check_source`), its pure rules (`detect_changes`, `plan_detail_fetches`), and the adapter and repository interfaces |
| `src/cv_job_match/profile.py`        | `load_profile`, `CandidateProfile` and the `ProfileRepository` interface |
| `src/cv_job_match/evaluation.py`     | `Evaluation` (`evaluate_waiting`, `evaluate_vacancy`), the Assessment schema, the repository and `Notifier` interfaces, and `DecisionButton` |
| `src/cv_job_match/decisions.py`      | `Decisions` (status changes and recent Assessed Vacancies) and the `DecisionRepository` interface |
| `src/cv_job_match/bot/handlers.py`   | `DecisionBot`, the chat handlers for buttons and `/list` |
| `src/cv_job_match/tailoring.py`      | `Tailoring` (`start_tailoring`, `tailor_application`), the Application Pack metadata, the PDF template rendering and the `TailoringRepository` interface |
| `src/cv_job_match/prefilter.py`      | `find_prefilter_reason`, the Pre-filter keyword rules |
| `src/cv_job_match/db/postgres.py`    | PostgreSQL repositories, `create_database_engine` and `migrate_database` |
| `src/cv_job_match/db/schema.py`      | The PostgreSQL schema as SQLAlchemy tables        |
| `src/cv_job_match/db/migrations/`    | Alembic migrations; `alembic.ini` is at the root  |
| `src/cv_job_match/bot/telegram.py`   | `TelegramNotifier`, the Vacancy button data and `create_dispatcher` (aiogram) |
| `src/cv_job_match/service.py`        | `serve`'s secrets check, scheduler and `run_service` |
| `src/cv_job_match/chat_model.py`     | `create_chat_model` from the `CV_JOB_MATCH_LLM_*` settings |
| `src/cv_job_match/adapters/`         | Technique adapters, wired by name in `build_app`  |
| `src/cv_job_match/http_client.py`    | `HttpClient` seam and `PoliteHttpClient`          |
| `src/cv_job_match/exceptions.py`     | Every custom exception                            |
| `src/cv_job_match/constants.py`      | Every module-level constant                       |
| `src/cv_job_match/cli.py`            | CLI; register new commands in `create_parser`     |
| `src/cv_job_match/utils/clock.py`    | `Clock` interface and `SystemClock`               |
| `src/cv_job_match/utils/logger.py`   | `configure_file_logging`, called once at startup  |

Modules never configure logging. Each one creates `logger = logging.getLogger(__name__)`, and
its records reach the log file through the `cv_job_match` package logger.
