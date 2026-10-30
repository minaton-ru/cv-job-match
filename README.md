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

For Sources whose list is server-side HTML. Values are read with CSS selectors; selector syntax
is checked when the Source is checked. Requests are GET, and one page is read.

```yaml
    list:
      url: https://careers.example.com/jobs   # required
      params: {q: python}                     # optional query parameters
      list_selector: ul.jobs                  # required; the element that holds the list
      item_selector: li.job                   # required; each item inside that element
      id_field: {selector: a, attribute: data-id}   # required; where the site's own ID is
      fields:                                 # optional; field name -> where its value is
        title: h3                             # plain selector: the element's text
        url: {selector: a, attribute: href}   # selector plus attribute: the attribute's value
    detail:                                   # optional; without it, no description is fetched
      url: https://careers.example.com/jobs/{id}   # {id} is the Vacancy's ID
      params: {lang: en}                      # optional; {id} is replaced in values too
      description_selectors: [div.summary, div.duties]  # every match, joined by blank lines
```

A value is read from the first element its `selector` matches inside the item; an empty or
missing `selector` means the item itself. Text has its inner whitespace collapsed. A page where
`list_selector` matches nothing, including a malformed or non-HTML page, makes the list fetch
fail; a list element with no items is an empty list.

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
language: en                  # required; language of Vacancies and Application Packs
score_threshold: 60           # optional, 0-100, default 60; lowest score that is sent
preferences: [Remote work]    # optional lists of text, default empty
excluded_roles: [Sales Manager]
strengths: [Python]
weaknesses: [Frontend]
prefilter:                    # optional; Pre-filter keyword rules
  title_keywords: [python, backend]
  excluded_title_keywords: [director]
  cities: [Example City]
```

Unknown keys are rejected, so a misspelled key is reported instead of ignored. List items must
not be blank.

The Profile version is a SHA-256 hash of both files' text, so the same content always gives the
same version and any edit, comments included, gives a new one.
`App.profile.save_snapshot_if_new()` saves the loaded Profile through `ProfileRepository` only
when its version is new. Production has no Profile repository yet, so for now it runs only in
tests, through an in-memory fake.

## Run

```sh
uv run cv-job-match check-config                  # validate the config and list the Sources
uv run cv-job-match --sources other.yaml check-config
uv run cv-job-match check-source example-backend  # dry-run one Source, saving nothing
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

`run_due` needs a Discovery repository. Production has none yet, so for now it runs only in
tests, through an in-memory fake.

Requests go through `PoliteHttpClient`: at most one request per second per host, with a
browser-like User-Agent. Redirects are not followed; a redirected list shows up as an
`HTTP 3xx` suspect reason, so point `url` at the final address.

### Logs

Every log of the service, from `CV_JOB_MATCH_LOG_LEVEL` up, goes to `cv_job_match.log` in the working directory.
The file is rotated at 10 MB, and the three previous files are kept as `cv_job_match.log.1` to
`.3`. Log files are excluded from Git.

## Develop

```sh
uv run ruff format .
uv run ruff check --fix .
uv run pytest -q
```

Everything is wired by one factory, `build_app(settings, *, repos, llm, http, notifier, clock)`
in `src/cv_job_match/app.py`. Production passes only settings, and tests pass Fake classes for
any dependency, such as `FakeHttpClient` in `tests/fakes.py` with canned responses per URL, or
`Repositories(discovery=FakeDiscoveryRepository(), profile=FakeProfileRepository())` for
in-memory stores. An autouse fixture in `tests/conftest.py` runs each test in its own temporary
directory with a synthetic `profile/` folder.

| Path                                 | Holds                                             |
| ------------------------------------ | ------------------------------------------------- |
| `src/cv_job_match/app.py`            | `App` and the `build_app` factory                 |
| `src/cv_job_match/settings.py`       | Settings read from the environment                |
| `src/cv_job_match/sources.py`        | Sources config schema and loader                  |
| `src/cv_job_match/discovery.py`      | `Discovery` (`run_due`, `check_source`), its pure rules (`detect_changes`, `plan_detail_fetches`), and the adapter and repository interfaces |
| `src/cv_job_match/profile.py`        | `load_profile`, `CandidateProfile` and the `ProfileRepository` interface |
| `src/cv_job_match/adapters/`         | Technique adapters, wired by name in `build_app`  |
| `src/cv_job_match/http_client.py`    | `HttpClient` seam and `PoliteHttpClient`          |
| `src/cv_job_match/exceptions.py`     | Every custom exception                            |
| `src/cv_job_match/constants.py`      | Every module-level constant                       |
| `src/cv_job_match/utils/cli.py`      | CLI; register new commands in `create_parser`     |
| `src/cv_job_match/utils/clock.py`    | `Clock` interface and `SystemClock`               |
| `src/cv_job_match/utils/logger.py`   | `configure_file_logging`, called once at startup  |

Modules never configure logging. Each one creates `logger = logging.getLogger(__name__)`, and
its records reach the log file through the `cv_job_match` package logger.
