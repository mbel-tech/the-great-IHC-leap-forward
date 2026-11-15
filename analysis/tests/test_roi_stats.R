# Checks for roi_stats.R. Plain R, stopifnot - no test framework.
#
# Run:  Rscript analysis/tests/test_roi_stats.R
#
# What is checked, and why each one is here:
#
#   1. The F line does not depend on the ORDER of the terms. anova() on an lm is
#      sequential (Type I), so on unbalanced cells - the study's are 3/2/3/3
#      animals - `treatment * environment` and `environment * treatment` give a
#      different F for treatment. The data set below is built so that they do,
#      and the line roi_stats.R reports must be the same either way.
#   2. With the full model, the overall p (which decides the asterisk when only
#      two groups are present) is the omnibus test of `value ~ group4`, not the
#      smallest of three term p values.
#   3. The Benjamini-Hochberg column is p.adjust(p, "BH") within each term.

args <- commandArgs(FALSE)
here <- dirname(sub("^--file=", "", grep("^--file=", args, value = TRUE)[1]))
source(file.path(here, "..", "roi_stats.R"))

ok <- function(what, cond) {
  if (!isTRUE(cond)) stop("FAIL: ", what, call. = FALSE)
  cat("PASS", what, "\n")
}

# ---- an unbalanced 2x2, one row per animal (3/2/3/3) ----------------------
d <- data.frame(
  sample = paste0("LS", 1:11),
  treatment = factor(c("control", "control", "control",
                       "exercise", "exercise",
                       "control", "control", "control",
                       "exercise", "exercise", "exercise")),
  environment = factor(c("brackish", "brackish", "brackish",
                         "brackish", "brackish",
                         "sea", "sea", "sea",
                         "sea", "sea", "sea"), levels = c("brackish", "sea")),
  value = c(10, 12, 11,   20, 23,   14, 13, 16,   15, 17, 19))
d$group4 <- factor(paste(d$environment, d$treatment),
                   levels = c("brackish control", "brackish exercise",
                              "sea control", "sea exercise"))

# The premise: sequential SS on these data DO move with the order. If they did
# not, check 1 would pass for the wrong reason.
a1 <- anova(lm(value ~ treatment * environment, d))
a2 <- anova(lm(value ~ environment * treatment, d))
ok("premise: Type I F for treatment depends on term order",
   abs(a1["treatment", "F value"] - a2["treatment", "F value"]) > 1e-6)

f1 <- lm(value ~ treatment * environment, d)
f2 <- lm(value ~ environment * treatment, d)
for (term in c("treatment", "environment")) {
  l1 <- term_line(f1, term)
  l2 <- term_line(f2, term)
  ok(sprintf("%s line is the same whichever order the terms are in", term),
     identical(as.character(l1), as.character(l2)))
  ok(sprintf("%s p is the same whichever order", term),
     isTRUE(all.equal(attr(l1, "p"), attr(l2, "p"))))
}
# The interaction is named by the order it was written in; same test either way.
ok("interaction p is the same whichever order",
   isTRUE(all.equal(attr(term_line(f1, "treatment:environment"), "p"),
                    attr(term_line(f2, "environment:treatment"), "p"))))

# The marginal test is the Type III one: the same as drop1() on sum-to-zero
# contrasts, which is the textbook definition.
op <- options(contrasts = c("contr.sum", "contr.poly"))
f3 <- lm(value ~ treatment * environment, d)
t3 <- drop1(f3, . ~ ., test = "F")
options(op)
ok("treatment p is the Type III p",
   isTRUE(all.equal(attr(term_line(f1, "treatment"), "p"),
                    t3["treatment", "Pr(>F)"], tolerance = 1e-8)))
# joint_tests reports F.ratio rounded to four significant figures (its p.value
# is not rounded), hence the looser tolerance on F alone.
ok("treatment F and df are carried on the line",
   isTRUE(all.equal(attr(term_line(f1, "treatment"), "F"),
                    t3["treatment", "F value"], tolerance = 1e-3)) &&
   attr(term_line(f1, "treatment"), "df1") == 1 &&
   attr(term_line(f1, "treatment"), "df2") == 7)

# Caption format is unchanged: F<sub>df1,df2</sub> = F, p.
ok("caption format",
   grepl("^treatment: F<sub>1,7</sub> = [0-9.]+, p (=|<) [0-9.]+$",
         as.character(term_line(f1, "treatment"))))
ok("an absent term is not estimable",
   identical(as.character(term_line(f1, "nonsense")), "nonsense: not estimable") &&
   is.na(attr(term_line(f1, "nonsense"), "p")))

# A BH-adjusted p sits beside the raw one when the caller has it.
l_adj <- term_line(f1, "treatment", p_adj = 0.048)
ok("BH-adjusted p is printed beside the raw one",
   grepl(", p (=|<) [0-9.]+, BH-adjusted 0.048$", as.character(l_adj)))
ok("...and the p attribute stays the raw p",
   identical(attr(l_adj, "p"), attr(term_line(f1, "treatment"), "p")))

# The df flag survives: one residual df.
tiny <- d[c(1, 2, 4, 6, 9), ]
lt <- term_line(lm(value ~ treatment * environment, tiny), "treatment")
ok("df too low is still flagged",
   grepl("(df too low to interpret)", as.character(lt), fixed = TRUE))

# ---- p_overall is the group4 omnibus ---------------------------------------
st <- stat_group4(d)
omni <- anova(lm(value ~ group4, d))["group4", "Pr(>F)"]
ok("full-model p_overall is the one-way omnibus p",
   isTRUE(all.equal(st$p, omni, tolerance = 1e-8)))
terms_p <- c(attr(term_line(f1, "treatment"), "p"),
             attr(term_line(f1, "environment"), "p"),
             attr(term_line(f1, "treatment:environment"), "p"))
ok("...and not the smallest term p", !isTRUE(all.equal(st$p, min(terms_p))))
ok("stat_group4 hands back one test row per term",
   identical(st$tests$term, c("treatment", "environment", "treatment:environment")))

st1 <- stat_treatment(d)
ok("stat_treatment hands back its test", identical(st1$tests$term, "treatment") &&
   isTRUE(all.equal(st1$tests$p, st1$p)))
st1b <- stat_treatment(d, p_adj = c(treatment = 0.5))
ok("stat_treatment prints a BH p it is given",
   grepl("BH-adjusted 0.500", st1b$caption, fixed = TRUE))
st4b <- stat_group4(d, p_adj = c(treatment = 0.2, environment = 0.3,
                                 "treatment:environment" = 0.4))
ok("stat_group4 prints a BH p on each term line",
   all(vapply(c("BH-adjusted 0.200", "BH-adjusted 0.300", "BH-adjusted 0.400"),
              grepl, logical(1), st4b$caption, fixed = TRUE)))

# ---- an empty cell -----------------------------------------------------------
# joint_tests() errors on a rank-deficient fit; the main effects must still be
# reported (from the additive model, order-free) and the interaction must not.
e <- d[d$group4 != "brackish exercise", ]
se <- stat_group4(e)
fa <- lm(value ~ treatment + environment, e)
ok("empty cell: treatment is still tested, from the additive model",
   isTRUE(all.equal(se$tests$p[se$tests$term == "treatment"],
                    drop1(fa, test = "F")["treatment", "Pr(>F)"], tolerance = 1e-8)))
ok("...the interaction says it is not estimable",
   grepl("interaction: not estimable", se$caption, fixed = TRUE) &&
   is.na(se$tests$p[se$tests$term == "treatment:environment"]))
ok("...and the caption says where the main effects came from",
   grepl("main effects from the additive model", se$caption, fixed = TRUE))
ok("...and the tests say so too", all(se$tests$model == "additive"))

# ---- two diagonal cells --------------------------------------------------------
# Both factors vary, but treatment fixes phase: the additive model cannot tell
# them apart, so no main effect may be reported - only the group test.
g <- d[d$group4 %in% c("brackish control", "sea exercise"), ]
sg <- stat_group4(g)
og <- anova(lm(value ~ group4, droplevels(g)))["group4", ]
ok("diagonal cells: no treatment or production-phase line",
   !grepl("treatment:", sg$caption, fixed = TRUE) &&
   !grepl("production phase:", sg$caption, fixed = TRUE))
ok("...one group line, the one-way F",
   # six animals in two groups: 1 and 4 df
   grepl(sprintf("group: F<sub>1,4</sub> = %.2f", og[["F value"]]),
         sg$caption, fixed = TRUE) &&
   identical(sg$tests$term, "group4") && identical(sg$tests$model, "oneway") &&
   isTRUE(all.equal(sg$tests$p, og[["Pr(>F)"]], tolerance = 1e-8)))
ok("...and the caption says why",
   grepl("coincide", sg$caption, fixed = TRUE))
ok("the full model's tests say full", all(st$tests$model == "full"))
ok("series 1's test says oneway", identical(st1$tests$model, "oneway"))

# ---- no denominator df -----------------------------------------------------------
# Forced through term_test, since no model here produces one; the line must not
# print "Inf" and must be flagged.
real_term_test <- term_test
term_test <- function(fit, term) list(F = 5, df1 = 1, df2 = Inf, p = 0.03,
                                      mixed = TRUE)
li <- term_line(f1, "treatment")
term_test <- real_term_test
ok("infinite df: not printed as Inf",
   !grepl("Inf", as.character(li), fixed = TRUE) &&
   grepl("^treatment: F<sub>1</sub> = 5.00, p = 0.030", as.character(li)))
ok("...and flagged", grepl("(no denominator df - do not interpret)",
                           as.character(li), fixed = TRUE))
ok("...and the df column is df1 alone",
   identical(test_rows(list(li), "treatment", "full")$df, "1"))

# ---- BH within term ---------------------------------------------------------
tests <- data.frame(
  roi = c("Dm", "Dm", "Vv", "Vv", "Dl", "Dl", "Hc"),
  term = c("treatment", "environment", "treatment", "environment",
           "treatment", "environment", "treatment"),
  F = 1, df = "1,7",
  p = c(0.01, 0.20, 0.04, 0.03, NA, 0.50, 0.02),
  stringsAsFactors = FALSE)
b <- add_bh(tests)
for (term in unique(tests$term)) {
  i <- tests$term == term
  ok(sprintf("BH for %s matches p.adjust", term),
     isTRUE(all.equal(b$p_bh[i], p.adjust(tests$p[i], "BH"))))
}
ok("BH keeps the row order and adds one column",
   identical(names(b), c(names(tests), "p_bh")) && identical(b$roi, tests$roi))
ok("an untested row stays NA", is.na(b$p_bh[5]))

cat("ALL PASS\n")
