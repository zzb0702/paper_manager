# -*- coding: utf-8 -*-
"""Build the deterministic sample library used by the retrieval evaluation.

Creates 10 synthetic papers (two confusable CRISPR papers, two attention
papers) as PDFs and ingests them with the free local engine, no LLM
summaries — so the eval baseline is deterministic and costs nothing.

The library lives in evals/library/ (PAPER_MANAGER_DATA_DIR), never in
your real data/ directory.

    python evals/build_library.py
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# must be set BEFORE importing paper_manager (config resolves it at import)
os.environ["PAPER_MANAGER_DATA_DIR"] = str(ROOT / "evals" / "library")
sys.path.insert(0, str(ROOT))

import pymupdf  # noqa: E402

from paper_manager.embedder import EmbeddingClient  # noqa: E402
from paper_manager.ingest import ingest_pdf  # noqa: E402

LIB = ROOT / "evals" / "library"
PDF_DIR = LIB / "pdfs"

# title, year, doi, author, body
PAPERS = [
    (
        "Attention Is All You Need: Transformer Networks",
        2017,
        "10.5555/3295222.3295349",
        "Vaswani et al",
        """Abstract
We propose the Transformer, a new architecture based solely on attention mechanisms, dispensing with recurrence and convolutions entirely. Experiments on two machine translation tasks show these models are superior in quality while being more parallelizable and requiring significantly less time to train. Our model achieves 28.4 BLEU on the WMT 2014 English-to-German translation task, improving over the existing best results by over 2 BLEU.

1 Introduction
Recurrent neural networks have long dominated sequence modeling and transduction problems. Nevertheless, their sequential nature precludes parallelization within training examples, which becomes critical at longer sequence lengths. Attention mechanisms have become an integral part of sequence modeling, allowing modeling of dependencies without regard to their distance in the input or output.

2 Background: Self-Attention
Self-attention, sometimes called intra-attention, relates different positions of a single sequence in order to compute a representation of the sequence. It has been used successfully in reading comprehension, abstractive summarization and sentence representation learning.

3 Method: Multi-Head Attention
We compute the scaled dot-product attention on a set of queries packed together into a matrix Q, with keys K and values V. Multi-head attention allows the model to jointly attend to information from different representation subspaces at different positions. We employ positional encodings to inject information about the relative or absolute position of tokens.

4 Experiments and Training Cost
On the WMT 2014 English-to-German task the big transformer achieves 28.4 BLEU. Training took 3.5 days on 8 P100 GPUs, a small fraction of the training costs of the best competing models. On English-to-French we achieve 41.8 BLEU at a cost of under 24 hours on 8 GPUs.
""",
    ),
    (
        "Efficient Attention: Linear Transformers for Long Sequences",
        2023,
        "10.1234/efficient-attn",
        "Chen et al",
        """Abstract
Self-attention scales quadratically with sequence length, dominating inference latency for long documents. We propose a linear-complexity attention based on random-feature kernel maps, achieving a 40x speedup on documents over 8k tokens with quality comparable to full attention.

1 Motivation
The quadratic cost of softmax attention restricts processing of long genomes, high-resolution images and full legal documents. Prior remedies include sparsity patterns and low-rank approximations, each sacrificing either speed or accuracy.

2 Method: Kernelized Attention
We replace the softmax with a positive random-feature kernel so that attention factors into a linear map: the values are aggregated with weights computed independently of the sequence length. This admits a recurrent-style inference with constant memory and parallel training.

3 Experiments on Long Sequences
On the Long Range Arena benchmark our kernel attention matches full attention quality on the Pathfinder task while running 40x faster at 16k tokens. Language modeling perplexity on books up to 32k context improves by 0.3 over a sliding-window baseline. Ablations show the positive feature map is essential; without it the estimate is biased.
""",
    ),
    (
        "Diffusion Models for Image Synthesis: A Survey",
        2024,
        "10.1234/diffusion-survey",
        "Rossi et al",
        """Abstract
Diffusion models have become the dominant paradigm for high-fidelity image generation. This survey unifies the mathematical framing of denoising diffusion probabilistic models, score-based generative models and stochastic differential equations, and compares training strategies, sampler designs and guidance techniques across ImageNet and MS-COCO benchmarks.

1 Foundations
Forward diffusion corrupts data with Gaussian noise over many steps; learning to reverse this process yields a generative model. The score-matching view reinterprets the denoiser as an estimate of the gradient of the data log-density, connecting diffusion to SDE solvers.

2 Guidance and Conditioning
Classifier guidance requires training a separate noise-aware classifier. Classifier-free guidance instead trains one conditional and one unconditional model jointly, then extrapolates the difference at sampling time, trading diversity for prompt fidelity with a single guidance scale parameter.

3 Samplers and Efficiency
Deterministic samplers such as DDIM reduce the hundred-step ancestral process to 10-20 steps. Distilled progressive samplers reach 1-4 steps at some quality cost. We find classifier-free guidance combined with a distilled sampler offers the best quality-latency tradeoff for deployment.
""",
    ),
    (
        "CRISPR-Cas9 for Disease-Resistant Staple Crops: A Field Review",
        2023,
        "10.1234/crispr-crops",
        "Liu et al",
        """Abstract
CRISPR-Cas9 has emerged as a versatile tool for targeted genome editing in crops. This field review covers applications of CRISPR systems for disease resistance, drought tolerance and yield improvement in rice, maize and wheat, summarizing 87 greenhouse and field studies.

1 Disease Resistance in Rice
Knockout of susceptibility genes conferred broad-spectrum resistance to bacterial blight across 14 field trials. Multiplex editing of three promoter regions produced durable resistance without measurable yield penalty in two seasons.

2 Tolerance and Yield in Maize and Wheat
Editing of arginine deaminase loci improved drought tolerance in maize under rain-fed conditions. In wheat, partial knockout of mildew resistance loci reduced powdery mildew severity by 70 percent while preserving grain protein content.

3 Delivery and Regulatory Outlook
Agrobacterium-mediated transformation remains the dominant delivery route for staple crops, while in planta editing reduces tissue culture bottlenecks. Regulatory acceptance of transgene-free edits is accelerating field deployment across Asia and South America.
""",
    ),
    (
        "Reducing Off-Target Mutations in CRISPR Screens via Guide RNA Design",
        2022,
        "10.1234/crispr-offtarget",
        "Wang et al",
        """Abstract
Unintended cleavage at off-target sites limits the safety of CRISPR therapies and the interpretability of screens. We present a guide RNA design pipeline that combines a deep learning off-target predictor with high-fidelity Cas9 variants, reducing detectable off-target mutations by an order of magnitude in cell lines.

1 Off-Target Prediction Model
Our convolutional model scores guide-target mismatches using sequence context and chromatin accessibility, improving the area under the precision-recall curve by 0.12 over GUIDES-based scoring on held-out GUIDE-seq datasets.

2 High-Fidelity Cas9 Variants
We benchmark SpCas9-HF1, eSpCas9 and HypaCas9 across 240 guides. HypaCas9 showed the cleanest specificity at on-target efficiency above 60 percent. Combining model-guided guide selection with HypaCas9 eliminated all detectable off-target events at 23 of 30 sites.

3 Clinical Implications
Ex vivo cell therapies benefit most immediately. We outline validation requirements: unbiased double-strand break mapping, long-read sequencing of candidate loci, and dose-limited toxicity studies before investigational new drug filings.
""",
    ),
    (
        "Graph Neural Networks for Molecular Property Prediction",
        2021,
        "10.1234/gnn-molecules",
        "Kim et al",
        """Abstract
Molecules are naturally expressed as graphs of atoms and bonds. We compare message passing neural networks, graph transformers and geometric equilibria models on solubility, toxicity and binding affinity prediction across six public benchmarks.

1 Message Passing Architectures
Message passing networks propagate atom features along bonds for several rounds and pool node states into graph embeddings. Deeper stacks oversmooth node representations; jump-knowledge connections and per-layer normalization recover performance.

2 Results on Solubility and Toxicity
On the ESOL solubility benchmark a 5-layer message passing network attains 0.58 mean absolute error in log units, matching pretrained graph transformers with 10x fewer parameters. Toxicity classification improves when atom features encode partial charge and aromaticity.

3 Beyond Static Graphs
Conformational flexibility matters for binding affinity. We overview 3D-aware variants that inject distance geometry, and highlight conformer ensembling as a low-cost accuracy gain for docking downstream tasks.
""",
    ),
    (
        "Reinforcement Learning from Human Feedback for Language Model Alignment",
        2023,
        "10.1234/rlhf-alignment",
        "Park et al",
        """Abstract
Alignment with human intent requires more than imitation. We study reinforcement learning from human feedback: training a reward model on pairwise preference data, then optimizing the language model against this reward with proximal policy optimization while constraining drift from the reference policy with a KL penalty.

1 Reward Modeling
Annotators compare candidate responses for helpfulness and harmlessness. A transformer scorer trained on 400k comparisons predicts preferences with 71 percent held-out accuracy; ensembling over seeds reduces reward hacking exploit surface.

2 Policy Optimization Dynamics
KL regularization against the supervised reference is essential: without it, policies overfit reward-model quirks within 200 steps, producing verbose degenerate text. We chart a stable region between KL coefficients and learning rates.

3 Evaluation
Human evaluators preferred RLHF-tuned responses over the supervised baseline in 63 percent of blind comparisons. Automated preference models correlate with human judgments at 0.8, enabling cheaper iteration on alignment datasets.
""",
    ),
    (
        "Knowledge Distillation for Compact Transformer Deployment",
        2022,
        "10.1234/distill-edge",
        "Nguyen et al",
        """Abstract
Deploying transformers on phones and embedded devices demands compression far beyond pruning. We systematically study knowledge distillation for compact transformers: token-level and logit-level losses, layer mapping strategies and curriculum schedules, yielding a 14x smaller student with 96 percent of teacher accuracy on sentiment tasks.

1 Distillation Losses
Combining hidden-state alignment with softened cross-entropy on teacher logits outperforms either alone. Temperature scaling between 4 and 8 works best; higher temperatures wash out discriminative structure.

2 Architecture and Layer Mapping
Students with uniform depth shrink gracefully; width reduction should be paired with an embedding projection. Skip-layer mapping preserves positional structure for 6-to-2 layer compressions.

3 On-Device Results
The distilled student runs at 38 milliseconds per sentence on a mid-range phone, 9x faster than the teacher, with no measurable accuracy regression on out-of-domain reviews. Quantization to int8 stacks with distillation for a further 3.4x memory cut.
""",
    ),
    (
        "Federated Learning with Differential Privacy across Hospitals",
        2021,
        "10.1234/fl-hospitals",
        "Garcia et al",
        """Abstract
Hospitals cannot pool patient records. We evaluate federated averaging with differential privacy for chest x-ray classification across 12 simulated hospital sites, showing that per-site noise calibration retains 94 percent of centralized accuracy at a privacy budget of epsilon 3.

1 Threat Model and Privacy Accounting
A curious server may inspect gradients. Gaussian-mechanism noise calibrated by the moments accountant bounds epsilon while clipping per-sample gradients caps any single patient's influence.

2 Training Dynamics Across Sites
Non-IID site distributions cause client drift; server momentum and a shared validation proxy set stabilize convergence. Site dropout of 30 percent delays but does not derail training.

3 Clinical Evaluation
The federated private model reaches AUC 0.91 for pneumonia detection versus 0.96 centralized. Auditors could not reconstruct chest x-rays from shared updates at the chosen noise scale, confirming the privacy-utility frontier is workable for multi-site studies.
""",
    ),
    (
        "Bayesian Optimization for Hyperparameter Search in Protein Engineering",
        2024,
        "10.1234/bo-protein",
        "Ivanov et al",
        """Abstract
Directed evolution explores enormous sequence spaces with scarce experimental budget. We apply Bayesian optimization with Gaussian process surrogates to protein hyperparameter and mutation search, tripling the rate of activity improvement over random mutagenesis on two enzyme families.

1 Surrogate Modeling
Sequence features from a protein language model feed a Gaussian process with a Matern kernel. Uncertainty estimates guide exploration; batch acquisitions such as q-expected-improvement amortize well-plate round costs.

2 Active Learning Campaigns
Across 9 rounds and 960 variants, Bayesian optimization found variants with 5.2x wild-type activity versus 1.7x for random search. The surrogate ranked top mutants correctly in 71 percent of plate validations.

3 Practical Guidance
Embedding choice dominates performance more than acquisition function. Retraining the language-model embedding on in-house assay data was the single highest-leverage investment for lab adoption.
""",
    ),
]


def make_pdf(path: Path, title: str, year: int, doi: str, author: str, body: str) -> None:
    doc = pymupdf.open()
    doc.set_metadata({"author": author})
    page, y = None, 0
    lines = [title, f"Year: {year}", ""] + body.splitlines(True) + ["", f"DOI: {doi}"]
    for line in lines:
        if page is None or y > 780:
            page = doc.new_page()
            y = 60
        page.insert_text((50, y), line.rstrip("\n") or " ", fontsize=10, fontname="helv")
        y += 14
    doc.save(str(path))
    doc.close()


def main() -> None:
    if LIB.exists():
        shutil.rmtree(LIB)
    PDF_DIR.mkdir(parents=True, exist_ok=True)

    reports = []
    for title, year, doi, author, body in PAPERS:
        safe = (
            title.split(":")[0].lower().replace(" ", "_")
            .replace("-", "_")[:40]
        )
        pdf = PDF_DIR / f"{safe}.pdf"
        make_pdf(pdf, title, year, doi, author, body)
        r = ingest_pdf(
            pdf, engine="local", make_summary=False,
            embedder=EmbeddingClient.from_env(),
        )
        reports.append(r)
        print(f"  {r['status']:9s} [{r.get('paper_id')}] {title[:60]}")

    ok = sum(1 for r in reports if r["status"] == "ok")
    print(f"\n评测库就绪: {ok}/{len(PAPERS)} 篇 @ {LIB}")


if __name__ == "__main__":
    main()
