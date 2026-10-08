"""The curated host implied by a sample's own NCBI taxon, shared by the ENA import and
`backfill.host_taxon`.

The host is not derivable from the taxonomy tree (`qiita.terminology_term` stores no
lineage, and `human gut metagenome` is not a descendant of *Homo sapiens*), so the
judgment is an explicit table.
"""

from __future__ import annotations

from collections.abc import Iterable

from qiita_common.models import NCBI_TAXONOMY_HUMAN_TERM_ID

# Keyed on the sample's OWN taxon, which for a metagenome names the environment it was
# drawn from. The value is the host that environment implies; None means it has no host.
#
# Deliberately NOT exhaustive over NCBI. A taxon that is absent here is not
# "assumed hostless" — it is UNRESOLVED, and the submit path aborts on it. Add a
# row only when the host is genuinely implied by the environment, and note why.
HOST_BY_SAMPLE_TAXON: dict[str, str | None] = {
    # A human gut metagenome is, by construction, drawn from a human gut.
    "408170": NCBI_TAXONOMY_HUMAN_TERM_ID,
    # Seawater has no host. This is a decision ('not applicable'), not a gap —
    # and it means such samples are NOT host-depleted. Whether human reads should
    # still be removed from a host-LESS sample for contamination/privacy reasons
    # is a separate question this table cannot express; it is tracked separately.
    "1561972": None,
    "646099": NCBI_TAXONOMY_HUMAN_TERM_ID,  # human metagenome: drawn from a human
    "539655": NCBI_TAXONOMY_HUMAN_TERM_ID,  # human skin metagenome: drawn from human skin
    "410661": "10090",  # mouse gut metagenome: drawn from a mouse gut
    "540485": "10090",  # mouse skin metagenome: drawn from mouse skin
    "410658": None,  # soil metagenome: bulk soil, no organism it was taken from
    "412755": None,  # marine sediment metagenome: sediment, not an organism
    "556182": None,  # freshwater sediment metagenome: sediment, not an organism
    "408172": None,  # marine metagenome: open water, like seawater
    "1504975": None,  # salt marsh metagenome: wetland sediment and water
    "1671699": None,  # sand metagenome: mineral substrate
    "527640": None,  # microbial mat metagenome: free-living community on a surface
    "496921": None,  # stromatolite metagenome: lithified microbial mat
    "1260732": None,  # coal metagenome: geologic deposit
    # Deliberately ABSENT: '256318' (the bare `metagenome` root). It names no
    # environment, so it implies no host. On the live data these are almost
    # entirely blanks, which rule 1 catches before this table is consulted; what
    # is left over is genuinely under-specified metadata and must be curated, not
    # guessed at here. Engineered environments (bioreactor, activated sludge, oil
    # field) are absent too: ENA records a host on a large share of them.
}

# Taxa that name an environment, never a host, whether or not the table gives them a host.
NON_HOST_TAXA: frozenset[str] = frozenset(HOST_BY_SAMPLE_TAXON) | {"256318"}


def implied_hosts(taxon_ids: Iterable[str]) -> dict[str, str | None]:
    """The table restricted to `taxon_ids`; absent means unresolved, None no host."""
    return {t: HOST_BY_SAMPLE_TAXON[t] for t in taxon_ids if t in HOST_BY_SAMPLE_TAXON}
