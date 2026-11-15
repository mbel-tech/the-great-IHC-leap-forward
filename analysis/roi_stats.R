# The models behind the figure labels.
#
# Kept out of the plotting file because this is the part worth reading on its
# own: what is being tested, on which unit, and what happens when it cannot be.
#
# ONE ROW PER ANIMAL, so the model is ordinary least squares:
#
#     lm(value ~ <fixed>)
#
# plot_roi_figures.R hands over 06c's per-animal table, where an animal's nuclei
# are already pooled over its discs and weighted by the area those discs
# covered. The animal is the experimental unit, so least squares is operating on
# that unit directly: this is the ANOVA the design asks for, not a fallback.
#
# THE MIXED BRANCH IS KEPT, and it is what makes the above safe rather than
# merely true today. Hand this file a section- or slide-level table and it fits
# lmer(value ~ <fixed> + (1 | sample)) instead of quietly reading 130 sections
# as 130 fish, which would treble the apparent sample size and shrink every p
# value with it. Which model ran is printed on the figure, so the two can never
# be confused for one another after the fact.
#
# CORRECTED AND RAW ARE NEVER COMPARED. They are the same animals measured two
# ways, so each gets its own fit and its own letters.
#
# WHAT IS TESTED. Every F line is a MARGINAL test of its term - each term
# adjusted for every other term in the model, the Type III question - read from
# emmeans::joint_tests() for lm and lmer alike:
#
#   series 1   value ~ treatment                      F for treatment
#   series 2   value ~ treatment * environment        F for treatment, for
#                                                     production phase, and for
#                                                     their interaction
#              value ~ group4   (one factor absent)   F for group
#
# Not anova(). On an lm that is SEQUENTIAL (Type I) sums of squares, and on
# these unbalanced cells (3/2/3/3 animals) the F for treatment changed with the
# order the terms were written in - while the lmer branch, through lmerTest,
# was already Type III. The two branches were testing different hypotheses
# under the same caption. One function now serves both.
#
# The single "was the four-group comparison significant?" p - used for the
# asterisk when only two groups are present - is the omnibus F of the one-way
# fit `value ~ group4`, the same fit the letters come from. It used to be the
# SMALLEST of the three term p values, which is a minimum over three tests and
# is not a p value of anything.
#
# MULTIPLICITY IS THE CALLER'S. One fit sees one ROI, so it cannot know how many
# regions were tested beside it. plot_roi_figures.R collects every ROI's tests
# for a series, measure and marker, adds a Benjamini-Hochberg column per term
# with add_bh() below, prints it beside the raw p in each caption and writes the
# lot to roi_tests_<series>.csv. The raw p is still what the line carries as its
# `p` attribute.
#
# WHEN A FIT FAILS IT SAYS SO. Cells here are 3/2/3/3 animals, and a ROI can
# have a group with no animal in it at all - in which case the interaction is
# inestimable. Every fit is wrapped, and the failure becomes the label. A
# caption that quietly disappears looks like a figure nobody tested.

suppressPackageStartupMessages({
  have <- vapply(c("lme4", "lmerTest", "emmeans", "multcomp", "multcompView"),
                 requireNamespace, logical(1), quietly = TRUE)
})
if (!all(have)) {
  stop("needs: ", paste(names(have)[!have], collapse = ", "),
       "\n  install.packages(c(\"lme4\",\"lmerTest\",\"emmeans\",\"multcomp\",\"multcompView\"))")
}
suppressPackageStartupMessages({
  library(lmerTest)
  library(emmeans)
  library(multcomp)
})
# emmeans warns about the estimability of unbalanced cells on every call; the
# imbalance is the design, not news, and the letters already encode it.
emm_options(msg.interaction = FALSE, msg.nesting = FALSE)

# Which model the data shape actually supports.
#
# A random intercept per animal needs animals with MORE THAN ONE row - with
# exactly one the animal effect and the residual are the same thing and lmer
# cannot separate them, so the fit fails outright. On the per-animal table every
# ROI is in that position, which is why lm is now the ordinary path here and not
# the exception it was when the rows were sections.
#
# lm is not a fallback with an asterisk: one observation per animal means
# ordinary least squares is already operating on the experimental unit, and it
# is exactly the ANOVA that was asked for. Reporting "could not be fitted" and
# drawing no test would throw away every real comparison to protect against a
# problem these data do not have.
#
# The caption says which was used, because it changes how the df read.
needs_mixed <- function(d) {
  per <- table(d$sample)
  length(per) > 1 && max(per) > 1
}

fit_model <- function(form_fixed, d) {
  mixed <- needs_mixed(d)
  f <- if (mixed) stats::update(form_fixed, . ~ . + (1 | sample)) else form_fixed
  fit <- tryCatch(
    suppressMessages(suppressWarnings(
      if (mixed) lmerTest::lmer(f, data = d) else stats::lm(f, data = d))),
    error = function(e) NULL)
  list(fit = fit, mixed = mixed)
}

fmt_p <- function(p) {
  if (!is.finite(p)) return("p = n/a")
  if (p < 0.001) "p < 0.001" else sprintf("p = %.3f", p)
}

# A one-line F test from a fitted model, for a named fixed term.
#
# The denominator df is the number worth looking at, and on the per-animal table
# it is simply n_animals - k: read it as the sample size the figure is entitled
# to. On a finer table it comes from Satterthwaite (lmerTest's default) and
# should still land near the number of ANIMALS - if it reports the number of
# rows, the random effect is not doing its job and the result is
# pseudoreplicated.
# Returns the formatted line, and carries the p value on it as an attribute so
# a caller can ask "was this significant?" without re-parsing the sentence it
# just built.
#
# THE TEST IS emmeans::joint_tests(), for both model classes. It tests each term
# on the reference grid with the cells weighted equally, which is the Type III
# hypothesis and does not depend on the order the formula lists the terms in.
# For lmer the denominator df are asked for as Satterthwaite explicitly:
# emmeans would otherwise use Kenward-Roger whenever pbkrtest happens to be
# installed, and which df a caption shows must not depend on what else is on
# the machine.
#
# A term joint_tests drops - zero estimable df, as for an interaction with an
# empty cell - comes back "not estimable", as it did from anova().
term_test <- function(fit, term) {
  jt <- tryCatch(suppressMessages(suppressWarnings(as.data.frame(
    if (inherits(fit, "merMod")) emmeans::joint_tests(fit, lmer.df = "satterthwaite")
    else emmeans::joint_tests(fit)))), error = function(e) NULL)
  if (is.null(jt) || !nrow(jt)) return(NULL)
  row <- jt[trimws(as.character(jt[["model term"]])) == term, , drop = FALSE]
  if (nrow(row) != 1) return(NULL)
  list(F = row[["F.ratio"]], df1 = row[["df1"]], df2 = row[["df2"]],
       p = row[["p.value"]], mixed = inherits(fit, "merMod"))
}

# `p_adj`, when given, is this term's Benjamini-Hochberg p across the ROIs of
# its series; it is printed beside the raw p and changes nothing else. The `p`
# attribute stays the RAW p, and F, df1 and df2 ride along as attributes so the
# caller can tabulate the test without re-parsing the sentence.
term_line <- function(fit, term, label = term, p_adj = NA_real_) {
  tt <- term_test(fit, term)
  if (is.null(tt)) {
    return(structure(sprintf("%s: not estimable", label), p = NA_real_,
                     F = NA_real_, df1 = NA_real_, df2 = NA_real_))
  }
  dfd <- tt$df2
  # Degrees of freedom go in a SUBSCRIPT with no brackets - F<sub>1,6.0</sub> -
  # which is the convention in print and is what ggtext's element_markdown()
  # renders. Everything downstream that shows a caption has to use
  # element_markdown, or these tags appear as literal text. Satterthwaite df are
  # fractional and keep a decimal; least-squares df are whole numbers.
  out <- sprintf(if (tt$mixed) "%s: F<sub>%.0f,%.1f</sub> = %.2f, %s"
                 else "%s: F<sub>%.0f,%.0f</sub> = %.2f, %s",
                 label, tt$df1, dfd, tt$F, fmt_p(tt$p))
  p_adj <- suppressWarnings(as.numeric(unname(p_adj))[1])
  if (isTRUE(is.finite(p_adj))) {
    out <- paste0(out, ", BH-adjusted ",
                  if (p_adj < 0.001) "< 0.001" else sprintf("%.3f", p_adj))
  }
  # Flag a p value nobody should act on. Vc raw came back F(1,1) = 915,
  # p = 0.021 while the corrected measure on the SAME animals gave p = 0.120 -
  # on one residual degree of freedom the estimate is essentially unconstrained
  # and a small p means almost nothing. Printing it without this reads as a
  # finding.
  if (is.finite(dfd) && dfd < 3) out <- paste(out, "(df too low to interpret)")
  structure(out, p = unname(tt$p), F = unname(tt$F),
            df1 = unname(tt$df1), df2 = unname(dfd))
}

# The tests behind a caption, one row per term, for the caller to adjust and
# write out. `df` is "df1,df2" as the caption prints it.
test_rows <- function(lines, terms) {
  data.frame(
    term = terms,
    F = vapply(lines, function(l) attr(l, "F"), numeric(1)),
    df = vapply(lines, function(l) {
      a <- attr(l, "df1"); b <- attr(l, "df2")
      if (is.finite(a) && is.finite(b)) paste0(format(a), ",", format(round(b, 1)))
      else NA_character_
    }, character(1)),
    p = vapply(lines, function(l) attr(l, "p"), numeric(1)),
    stringsAsFactors = FALSE, row.names = NULL)
}

# Whether the fit could not estimate every fixed coefficient - an empty cell
# under an interaction. lm keeps the coefficient as NA; lmer drops the column
# and records which.
rank_deficient <- function(fit) {
  if (inherits(fit, "merMod")) !is.null(attr(fit@pp$X, "col.dropped"))
  else anyNA(stats::coef(fit))
}

# Benjamini-Hochberg across the ROIs of one series, measure and marker, done
# separately for each TERM: the treatment tests of ten ROIs are one family, the
# interaction tests another. Mixing terms into one family would let a strong
# production-phase effect loosen the treatment threshold. Rows without a p
# (not estimable) stay NA and do not count towards the family size - which is
# what p.adjust does with NA.
add_bh <- function(tests) {
  tests$p_bh <- rep(NA_real_, nrow(tests))   # a bare NA will not fit 0 rows
  for (t in unique(tests$term)) {
    i <- which(tests$term == t)
    tests$p_bh[i] <- stats::p.adjust(tests$p[i], method = "BH")
  }
  tests
}

# --------------------------------------------------------------- series 1
# density ~ treatment, one caption line per measure.
#
# `p_adj` is a named vector of BH-adjusted p by term (here only "treatment"),
# from add_bh() over every ROI of the series; NULL on the first pass, when the
# caller is still collecting the raw tests. $tests is that collection's input:
# one row per term tested, none when nothing was.
NO_TESTS <- data.frame(term = character(0), F = numeric(0), df = character(0),
                       p = numeric(0), stringsAsFactors = FALSE)

stat_treatment <- function(d, p_adj = NULL) {
  n_an <- length(unique(d$sample))
  if (n_an < 3 || length(unique(d$treatment)) < 2) {
    return(list(caption = sprintf("no test: %d animal%s in %d treatment group%s",
                                  n_an, if (n_an == 1) "" else "s",
                                  length(unique(d$treatment)),
                                  if (length(unique(d$treatment)) == 1) "" else "s"),
                p = NA_real_, tests = NO_TESTS))
  }
  m <- fit_model(value ~ treatment, d)
  if (is.null(m$fit)) {
    return(list(caption = "no test: the model could not be fitted", p = NA_real_,
                tests = NO_TESTS))
  }
  line <- term_line(m$fit, "treatment", "treatment",
                    p_adj = if ("treatment" %in% names(p_adj)) p_adj[["treatment"]]
                            else NA_real_)
  list(caption = paste0(line, "<br>",
                        if (m$mixed) "mixed model, animal as a random effect"
                        else "one point per animal, plain ANOVA"),
       p = attr(line, "p"),
       tests = test_rows(list(line), "treatment"))
}

# --------------------------------------------------------------- series 2
# density ~ treatment * environment, plus Tukey letters over the four groups.
#
# Returns a list: $caption (three F lines) and $letters (a data.frame of
# group -> letter), or $letters = NULL when the model would not fit.
#
# `p_adj` and $tests as for stat_treatment, by term: "treatment", "environment"
# and "treatment:environment" for the full model, "group4" otherwise.
stat_group4 <- function(d, p_adj = NULL) {
  cells <- table(droplevels(d$group4))
  present <- sum(cells > 0)
  n_an <- length(unique(d$sample))
  if (present < 2 || n_an < 4) {
    return(list(caption = sprintf("no test: %d of 4 groups present, %d animals",
                                  present, n_an),
                letters = NULL, n_groups = present, p = NA_real_,
                tests = NO_TESTS))
  }

  full <- length(unique(d$treatment)) > 1 && length(unique(d$environment)) > 1
  m <- fit_model(if (full) value ~ treatment * environment else value ~ group4, d)
  fit <- m$fit
  if (is.null(fit)) {
    return(list(caption = "no test: the model could not be fitted",
                letters = NULL, n_groups = present, p = NA_real_,
                tests = NO_TESTS))
  }

  # One term per line. Three F tests joined by pipes ran off the right edge of
  # the figure and had to be rescued by widening it; as lines they simply fit.
  #
  # The first column is the term name joint_tests() actually uses; only the
  # second is the label shown. Changing the lookup to a prettier string makes
  # every interaction line read "not estimable".
  terms <- if (full) c("treatment", "environment", "treatment:environment")
           else "group4"
  labels <- if (full) c("treatment", "production phase", "interaction") else "group"

  # AN EMPTY CELL TAKES THE INTERACTION WITH IT, and joint_tests() does not
  # degrade gracefully: on a rank-deficient fit (lm leaves the coefficient NA,
  # lmer drops the column) it stops with an rbind error, which would turn all
  # three lines into "not estimable". anova() used to print the two main
  # effects there - sequentially. The order-free equivalent is the ADDITIVE
  # model, where each main effect is adjusted for the other and nothing is
  # assumed about the missing cell; the interaction line still says it could
  # not be estimated, and one more line says where the main effects came from.
  fit_t <- fit
  additive <- full && rank_deficient(fit)
  if (additive) {
    fit_t <- fit_model(value ~ treatment + environment, d)$fit
    if (is.null(fit_t)) fit_t <- fit
  }
  lines <- Map(function(t, l) term_line(
                 fit_t, t, l, p_adj = if (t %in% names(p_adj)) p_adj[[t]] else NA_real_),
               terms, labels)
  tests <- test_rows(lines, terms)
  cap <- paste(unlist(lines), collapse = "<br>")
  if (additive) cap <- paste0(
    cap, "<br>a group has no animals: main effects from the additive model")
  # A singular fit means the animal variance collapsed to zero. The letters are
  # still computed - the fixed effects are fine - but it is said out loud,
  # because it is the model telling you the design is thin.
  cap <- paste0(cap, "<br>", if (m$mixed) "mixed model, animal as a random effect"
                             else "one point per animal, plain ANOVA")
  # A singular fit is not a cosmetic warning. The animal variance has collapsed
  # to zero, so the random effect stops absorbing anything and the denominator
  # df run to the ROW count rather than the animal count. The p value is then
  # the pseudoreplicated one, which is the thing the mixed model was chosen to
  # avoid, so the caption says so instead of printing "(singular fit)" and
  # leaving the reader to know what that implies.
  #
  # It cannot fire on the per-animal table - one row per animal never reaches
  # lmer - and it is kept for the finer tables where it used to fire on four of
  # the ten ROIs.
  if (m$mixed && isTRUE(lme4::isSingular(fit))) {
    cap <- paste0(cap, "<br>SINGULAR FIT: animal variance collapsed to zero, so ",
                  "the df above are per row, not per animal - treat this p as ",
                  "anti-conservative")
  }

  # THE LETTERS COME FROM THE ONE-WAY PARAMETERISATION, and that is not a second
  # analysis. `~ treatment * environment` and `~ group4` span the identical
  # design space - four cells, four parameters - so it is the same fit written
  # two ways. Comparing a single 4-level factor makes the six pairwise contrasts
  # unambiguously ONE family, which is the condition Tukey is defined for.
  #
  # emmeans prints "adjust = tukey was changed to sidak" here and it is easy to
  # misread as the letters being downgraded. They are not. The full output says
  #
  #     Conf-level adjustment: sidak method for 4 estimates
  #     P value adjustment:    tukey method for comparing a family of 4 estimates
  #
  # - Sidak applies to the confidence intervals cld also reports, which nothing
  # here uses, and the PAIRWISE p values that decide the letters are Tukey.
  fit1 <- if (full) fit_model(value ~ group4, d)$fit else fit
  lets <- tryCatch({
    em <- emmeans(fit1, ~ group4)
    # TUKEY ON ONE RESIDUAL DEGREE OF FREEDOM RETURNS NaN, and cld does not
    # object: it files every group under "a", which on the figure is
    # indistinguishable from "tested, and nothing differs". The per-animal table
    # reaches that state wherever four animals fall in three groups - Vc and Vs
    # here - so the p values are checked before the letters are believed, and
    # the caption says the comparison was not estimable rather than reporting a
    # null result nobody computed.
    pv <- suppressWarnings(as.data.frame(pairs(em, adjust = "tukey"))$p.value)
    if (!length(pv) || !all(is.finite(pv))) stop("tukey p values not estimable")
    cl <- as.data.frame(multcomp::cld(em, alpha = 0.05, Letters = letters,
                                      adjust = "tukey"))
    data.frame(group4 = as.character(cl$group4),
               letter = trimws(cl$.group),
               emmean = cl$emmean,
               stringsAsFactors = FALSE)
  }, error = function(e) NULL)

  # On its own line, like every other caption line: joined with a space it
  # lengthened the last F line past the panel edge, where ggtext clips it.
  if (is.null(lets)) cap <- paste0(
    cap, "<br>Tukey: not estimable on these cells, so no letters are drawn")

  # With only TWO groups present, compact letters carry no information a reader
  # cannot get from a single mark: "a / b" just means significant. The figure
  # draws an asterisk on the higher group instead, so it needs the overall p.
  #
  # That p is the omnibus F of the one-way fit - "do the groups present differ
  # at all?" - which is what an asterisk between two groups claims. It is the
  # same fit the letters come from, and for a reduced model it is the model
  # itself. The minimum of the three term p values it replaced was not the p of
  # any test, and was smaller than any of them by construction.
  p_overall <- if (is.null(fit1)) NA_real_ else attr(term_line(fit1, "group4"), "p")
  if (!isTRUE(is.finite(p_overall))) p_overall <- NA_real_

  list(caption = cap, letters = lets, n_groups = present, p = p_overall,
       tests = tests)
}
