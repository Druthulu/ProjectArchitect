"""pa.fit -- percent-per-dollar fit per window instance (design doc E.3/E.4).

Pure functions; sqlite imported only inside the db-touching helpers.
"""


# --------------------------------------------------------------------------- families

FAMILIES = ("fable", "opus", "sonnet", "haiku")


def family_of(model):
    """Map a model id to a family name via ``prices.normalize_model`` (substring match).

    Returns one of :data:`FAMILIES` or ``"other"``.
    """
    from . import prices as _prices

    nm = _prices.normalize_model(model)
    for fam in FAMILIES:
        if fam in nm:
            return fam
    return "other"


# --------------------------------------------------------------------------- crossings

def crossings(samples, eps=0.05):
    """Every consecutive pair with pct_b > pct_a and cost_b >= cost_a is one crossing.

    ``samples`` = [(epoch, pct, cost)] of one instance sorted by epoch.
    A multi-level jump yields one crossing at the top level.
    Returns list of dicts with level, lo, hi, mid, half.
    """
    out = []
    for i in range(1, len(samples)):
        ep_a, pct_a, cost_a = samples[i - 1]
        ep_b, pct_b, cost_b = samples[i]
        if pct_b > pct_a and cost_b >= cost_a:
            lo = cost_a
            hi = cost_b
            mid = (lo + hi) / 2.0
            half = max((hi - lo) / 2.0, eps)
            out.append({"level": pct_b / 100.0, "lo": lo, "hi": hi,
                        "mid": mid, "half": half})
    return out


# --------------------------------------------------------------------------- OLS helpers

def _weighted_ols(xs, ys, ws):
    """Weighted OLS y ~ a + b*x.  Returns (intercept, slope, se_slope) or None."""
    n = len(xs)
    if n < 2:
        return None
    sw = sum(ws)
    if sw <= 0:
        return None
    swx = sum(w * x for w, x in zip(ws, xs))
    swy = sum(w * y for w, y in zip(ws, ys))
    swx2 = sum(w * x * x for w, x in zip(ws, xs))
    swxy = sum(w * x * y for w, x, y in zip(ws, xs, ys))
    denom = sw * swx2 - swx * swx
    if abs(denom) < 1e-30:
        return None
    b = (sw * swxy - swx * swy) / denom
    a = (swy - b * swx) / sw
    # residual variance
    ssr = sum(w * (y - a - b * x) ** 2 for w, x, y in zip(ws, xs, ys))
    df = n - 2
    if df <= 0:
        return a, b, 0.0
    mse = ssr / df
    var_b = mse * sw / denom
    se = var_b ** 0.5 if var_b > 0 else 0.0
    return a, b, se


def crossing_fit(cr, min_crossings=3):
    """Weighted OLS level ~ a + b*mid, w = 1/half^2.

    Returns {slope, intercept, se, n} or None.
    """
    if len(cr) < min_crossings:
        return None
    xs = [c["mid"] for c in cr]
    ys = [c["level"] for c in cr]
    ws = [1.0 / (c["half"] ** 2) for c in cr]
    result = _weighted_ols(xs, ys, ws)
    if result is None:
        return None
    intercept, slope, se = result
    return {"slope": slope, "intercept": intercept, "se": se, "n": len(cr)}


def _weighted_ols_multi(Xs, ys, ws):
    """Weighted OLS y ~ a + b1*x1 + ... + bk*xk via Gaussian elimination on the normal equations.

    ``Xs`` = list of rows, each row a list of k regressors.
    Returns ``(intercept, [slopes], [se_slopes])`` or None.
    At most a 5x5 system (intercept + 4 families).
    """
    if not Xs or not ys:
        return None
    n = len(ys)
    k = len(Xs[0])
    p = k + 1  # intercept + k slopes
    if n < p + 1:
        return None
    # Build the (p x p) normal equations  A'WA b = A'Wy
    # A has columns [1, x1, x2, ..., xk]
    ata = [[0.0] * p for _ in range(p)]
    aty = [0.0] * p
    for i in range(n):
        w = ws[i]
        row = [1.0] + list(Xs[i])
        for r in range(p):
            for c in range(p):
                ata[r][c] += w * row[r] * row[c]
            aty[r] += w * row[r] * ys[i]
    # Augmented matrix for Gaussian elimination
    aug = [ata[r][:] + [aty[r]] for r in range(p)]
    for col in range(p):
        # partial pivoting
        best = col
        for r in range(col + 1, p):
            if abs(aug[r][col]) > abs(aug[best][col]):
                best = r
        aug[col], aug[best] = aug[best], aug[col]
        piv = aug[col][col]
        if abs(piv) < 1e-30:
            return None
        for c in range(col, p + 1):
            aug[col][c] /= piv
        for r in range(p):
            if r == col:
                continue
            f = aug[r][col]
            for c in range(col, p + 1):
                aug[r][c] -= f * aug[col][c]
    beta = [aug[r][p] for r in range(p)]
    intercept = beta[0]
    slopes = beta[1:]
    # standard errors from the residual variance and the inverse of A'WA
    # (the matrix is already identity after elimination, so its inverse is I;
    #  we need the diagonal of (A'WA)^-1, recompute)
    ssr = 0.0
    for i in range(n):
        row = [1.0] + list(Xs[i])
        pred = sum(beta[j] * row[j] for j in range(p))
        ssr += ws[i] * (ys[i] - pred) ** 2
    df = n - p
    if df <= 0:
        return intercept, slopes, [0.0] * k
    mse = ssr / df
    # Rebuild A'WA and invert for diag
    ata2 = [[0.0] * p for _ in range(p)]
    for i in range(n):
        w = ws[i]
        row = [1.0] + list(Xs[i])
        for r in range(p):
            for c in range(p):
                ata2[r][c] += w * row[r] * row[c]
    # Invert via Gauss-Jordan
    inv = [[0.0] * p for _ in range(p)]
    for r in range(p):
        inv[r][r] = 1.0
    aug2 = [ata2[r][:] + inv[r][:] for r in range(p)]
    for col in range(p):
        best = col
        for r in range(col + 1, p):
            if abs(aug2[r][col]) > abs(aug2[best][col]):
                best = r
        aug2[col], aug2[best] = aug2[best], aug2[col]
        piv = aug2[col][col]
        if abs(piv) < 1e-30:
            return intercept, slopes, [0.0] * k
        for c in range(2 * p):
            aug2[col][c] /= piv
        for r in range(p):
            if r == col:
                continue
            f = aug2[r][col]
            for c in range(2 * p):
                aug2[r][c] -= f * aug2[col][c]
    diag = [aug2[r][p + r] for r in range(p)]
    se_slopes = [(mse * diag[j + 1]) ** 0.5 if mse * diag[j + 1] > 0 else 0.0 for j in range(k)]
    return intercept, slopes, se_slopes


def crossings_multi(samples, families, eps=0.05):
    """Crossings with per-family cost brackets.

    ``samples`` = [(epoch, pct, {family: cost})] sorted by epoch.
    Returns list of dicts with level, lo, hi, mid, half and per-family mid values.
    """
    out = []
    # total cost for bracket computation
    for i in range(1, len(samples)):
        ep_a, pct_a, costs_a = samples[i - 1]
        ep_b, pct_b, costs_b = samples[i]
        if pct_b > pct_a:
            total_a = sum(costs_a.values())
            total_b = sum(costs_b.values())
            if total_b < total_a:
                continue
            lo = total_a
            hi = total_b
            mid = (lo + hi) / 2.0
            half = max((hi - lo) / 2.0, eps)
            # per-family mid
            fam_mids = {}
            for fam in families:
                fa = costs_a.get(fam, 0.0)
                fb = costs_b.get(fam, 0.0)
                fam_mids[fam] = (fa + fb) / 2.0
            out.append({"level": pct_b / 100.0, "lo": lo, "hi": hi,
                        "mid": mid, "half": half, "fam_mids": fam_mids})
    return out


def level_ols(samples, n_min=8, min_span_pct=3):
    """OLS pct/100 ~ a + b*cost over all samples.

    Returns {slope, intercept, se, n} or None when below minimums.
    """
    if len(samples) < n_min:
        return None
    pcts = [s[1] for s in samples]
    span = max(pcts) - min(pcts)
    if span < min_span_pct:
        return None
    xs = [s[2] for s in samples]
    ys = [s[1] / 100.0 for s in samples]
    ws = [1.0] * len(samples)
    result = _weighted_ols(xs, ys, ws)
    if result is None:
        return None
    intercept, slope, se = result
    return {"slope": slope, "intercept": intercept, "se": se, "n": len(samples)}


# --------------------------------------------------------------------------- fit_instance

def _is_quantized(samples):
    """True when every pct is integral."""
    for _, pct, _ in samples:
        if pct != int(pct):
            return False
    return True


def _span_pct(samples):
    if not samples:
        return 0
    pcts = [s[1] for s in samples]
    return max(pcts) - min(pcts)


def _mature_quality(samples, span, cfg):
    """T32.1: "ok" only with >= instance_min_samples, span >= instance_min_span_pct
    and epochs spanning >= instance_min_age_s; else "band"."""
    from . import config as _config
    n_req = _config.get(cfg, "fit.instance_min_samples", 50)
    span_req = _config.get(cfg, "fit.instance_min_span_pct", 10)
    age_req = _config.get(cfg, "fit.instance_min_age_s", 86400)
    eps = [s[0] for s in samples]
    age = (max(eps) - min(eps)) if eps else 0
    if len(samples) >= n_req and span >= span_req and age >= age_req:
        return "ok"
    return "band"


def _dominant_family(samples):
    """The family carrying the most total cost in multi-family samples."""
    totals = {}
    for _, _, costs in samples:
        if isinstance(costs, dict):
            for fam, c in costs.items():
                totals[fam] = totals.get(fam, 0.0) + c
    if not totals:
        return None
    return max(totals, key=totals.get)


def _single_slope_samples(samples):
    """Convert multi-family samples to old-style (epoch, pct, total_cost)."""
    out = []
    for ep, pct, costs in samples:
        if isinstance(costs, dict):
            out.append((ep, pct, sum(costs.values())))
        else:
            out.append((ep, pct, costs))
    return out


def fit_instance(samples, cfg=None):
    """Fit one window instance.

    ``samples``: ``(epoch, pct, cost)`` where ``cost`` is a float (legacy) or
    ``{family: cumulative_cost}`` (T2.1 multi-family).  When at least two
    families carry >= ``fit.family_min_usd`` inside the instance, a per-family
    weighted OLS is fitted; otherwise the single-slope fit applies and its
    slope is assigned to the dominant family.

    Returns dict with method, slope, intercept, se, n, crossings, span_pct,
    quality, quantized, dollars_per_window, families (dict of per-family
    {weight, se, dollars_per_window} or None).
    """
    from . import config as _config

    if cfg is None:
        cfg = {}
    min_crossings = _config.get(cfg, "fit.min_crossings", 3)
    good_crossings = _config.get(cfg, "fit.good_crossings", 5)
    n_min = _config.get(cfg, "fit.n_min", 8)
    min_span = _config.get(cfg, "fit.min_span_pct", 3)
    good_span = _config.get(cfg, "fit.good_span_pct", 5)
    eps = _config.get(cfg, "fit.bracket_eps_usd", 0.05)
    family_min_usd = _config.get(cfg, "fit.family_min_usd", 5)

    quantized = _is_quantized(samples)
    span = _span_pct(samples)
    result = {"method": None, "slope": None, "intercept": None, "se": None,
              "n": 0, "crossings": 0, "span_pct": span, "quality": "none",
              "quantized": quantized, "dollars_per_window": None,
              "families": None}

    # Detect multi-family samples
    is_multi = (samples and isinstance(samples[0][2], dict))

    # Check which families carry enough cost for a multi-family fit
    active_families = []
    if is_multi:
        fam_totals = {}
        for _, _, costs in samples:
            for fam, c in costs.items():
                fam_totals[fam] = fam_totals.get(fam, 0.0) + max(0, c)
        # max cumulative cost per family (not sum of deltas)
        fam_max = {}
        for _, _, costs in samples:
            for fam, c in costs.items():
                fam_max[fam] = max(fam_max.get(fam, 0.0), c)
        active_families = [f for f in FAMILIES if fam_max.get(f, 0.0) >= family_min_usd]

    if is_multi and len(active_families) >= 2:
        # Multi-family fit: pct/100 = a + sum_f w_f * C_f
        return _fit_multi(samples, active_families, cfg, quantized, span,
                          min_crossings, good_crossings, n_min, min_span,
                          good_span, eps)

    # Single-slope fit (legacy path or single-family fallback)
    if is_multi:
        flat_samples = _single_slope_samples(samples)
    else:
        flat_samples = samples

    fit = None
    cr = []
    if quantized:
        cr = crossings(flat_samples, eps)
        if len(cr) >= min_crossings:
            fit = crossing_fit(cr, min_crossings)
            if fit and fit["slope"] > 0:
                result.update({"method": "crossing", "slope": fit["slope"],
                               "intercept": fit["intercept"], "se": fit["se"],
                               "n": fit["n"], "crossings": len(cr)})
            else:
                fit = None

    if fit is None:
        fit = level_ols(flat_samples, n_min, min_span)
        if fit and fit["slope"] > 0:
            result.update({"method": "level", "slope": fit["slope"],
                           "intercept": fit["intercept"], "se": fit["se"],
                           "n": fit["n"], "crossings": len(cr)})
        else:
            fit = None

    if fit is None:
        result["quality"] = "none"
        return result

    # dollars_per_window = 1 / slope
    if result["slope"] and result["slope"] > 0:
        result["dollars_per_window"] = 1.0 / result["slope"]

    # quality (T32.1: instance maturity; was good_crossings/2*n_min with good_span)
    result["quality"] = _mature_quality(samples, span, cfg)

    # Assign the single slope to the dominant family
    if is_multi:
        dom = _dominant_family(samples)
        if dom and result["slope"]:
            result["families"] = {dom: {"weight": result["slope"],
                                        "se": result.get("se"),
                                        "dollars_per_window": result["dollars_per_window"]}}

    return result


def _fit_multi(samples, active_families, cfg, quantized, span,
               min_crossings, good_crossings, n_min, min_span,
               good_span, eps):
    """Multi-family weighted OLS: pct/100 = a + Σ w_f·C_f."""
    result = {"method": None, "slope": None, "intercept": None, "se": None,
              "n": 0, "crossings": 0, "span_pct": span, "quality": "none",
              "quantized": quantized, "dollars_per_window": None,
              "families": None}

    # Try the crossing-bracket fit first (quantized data)
    fit_ok = False
    cr = []
    if quantized:
        cr = crossings_multi(samples, active_families, eps)
        if len(cr) >= min_crossings:
            # Build the multi-regressor crossing fit
            ys = [c["level"] for c in cr]
            Xs = [[c["fam_mids"].get(f, 0.0) for f in active_families] for c in cr]
            ws = [1.0 / (c["half"] ** 2) for c in cr]
            mfit = _weighted_ols_multi(Xs, ys, ws)
            if mfit is not None:
                intercept, slopes, ses = mfit
                # Check for negative weights: refit without those families
                neg = [i for i, s in enumerate(slopes) if s <= 0]
                if neg:
                    keep = [f for i, f in enumerate(active_families) if i not in neg]
                    if len(keep) >= 1:
                        Xs2 = [[c["fam_mids"].get(f, 0.0) for f in keep] for c in cr]
                        mfit2 = _weighted_ols_multi(Xs2, ys, ws)
                        if mfit2 is not None:
                            intercept, slopes, ses = mfit2
                            active_families = keep
                            neg = [i for i, s in enumerate(slopes) if s <= 0]
                if not neg or all(s > 0 for s in slopes):
                    fit_ok = True
                    result["method"] = "crossing"
                    result["n"] = len(cr)
                    result["crossings"] = len(cr)

    # Level OLS fallback for multi-family
    if not fit_ok:
        if len(samples) >= n_min and span >= min_span:
            ys = [s[1] / 100.0 for s in samples]
            Xs = [[s[2].get(f, 0.0) for f in active_families] for s in samples]
            ws = [1.0] * len(samples)
            mfit = _weighted_ols_multi(Xs, ys, ws)
            if mfit is not None:
                intercept, slopes, ses = mfit
                neg = [i for i, s in enumerate(slopes) if s <= 0]
                if neg:
                    keep = [f for i, f in enumerate(active_families) if i not in neg]
                    if len(keep) >= 1:
                        Xs2 = [[s[2].get(f, 0.0) for f in keep] for s in samples]
                        mfit2 = _weighted_ols_multi(Xs2, ys, ws)
                        if mfit2 is not None:
                            intercept, slopes, ses = mfit2
                            active_families = keep
                            neg = [i for i, s in enumerate(slopes) if s <= 0]
                if not neg or all(s > 0 for s in slopes):
                    fit_ok = True
                    result["method"] = "level"
                    result["n"] = len(samples)
                    result["crossings"] = len(cr)

    if not fit_ok:
        # Fall back to the single-slope path
        flat = _single_slope_samples(samples)
        from . import config as _config
        return fit_instance(flat, cfg)

    # Build the per-family result
    fam_dict = {}
    for i, fam in enumerate(active_families):
        w = slopes[i]
        dpw = (1.0 / w) if w > 0 else None
        fam_dict[fam] = {"weight": w, "se": ses[i], "dollars_per_window": dpw}
    result["families"] = fam_dict
    result["intercept"] = intercept
    # The "slope" field: use the Fable weight when present, else the dominant family's
    dom = "fable" if "fable" in fam_dict else max(fam_dict, key=lambda f: fam_dict[f]["weight"])
    result["slope"] = fam_dict[dom]["weight"]
    result["se"] = fam_dict[dom]["se"]
    if result["slope"] and result["slope"] > 0:
        result["dollars_per_window"] = 1.0 / result["slope"]

    # quality (T32.1: instance maturity; was good_crossings/2*n_min with good_span)
    result["quality"] = _mature_quality(samples, span, cfg)

    return result


# --------------------------------------------------------------------------- pooled_slope / pooled_weights

def pooled_slope(fits, cfg=None):
    """Inverse-variance mean of the last ``pool_instances`` fits with slope > 0 and se > 0.

    Falls back to plain mean when se is missing.  Returns a dict like fit_instance
    with quality 'band' or None.
    """
    from . import config as _config

    if cfg is None:
        cfg = {}
    pool_n = _config.get(cfg, "fit.pool_instances", 3)
    eligible = [f for f in fits if f.get("slope") and f["slope"] > 0]
    eligible = eligible[-pool_n:]
    if not eligible:
        return None
    have_se = all(f.get("se") and f["se"] > 0 for f in eligible)
    if have_se:
        weights = [1.0 / (f["se"] ** 2) for f in eligible]
        sw = sum(weights)
        slope = sum(w * f["slope"] for w, f in zip(weights, eligible)) / sw
        # pooled se: 1/sqrt(sum(1/se^2))
        se = (1.0 / sw) ** 0.5
    else:
        slope = sum(f["slope"] for f in eligible) / len(eligible)
        se = None
    return {"method": "pooled", "slope": slope, "intercept": None,
            "se": se, "n": sum(f.get("n", 0) for f in eligible),
            "crossings": sum(f.get("crossings", 0) for f in eligible),
            "span_pct": max((f.get("span_pct") or 0) for f in eligible),
            "quality": "band", "quantized": None,
            "dollars_per_window": (1.0 / slope) if slope > 0 else None}


def pooled_weights(fits, cfg=None):
    """Per-family inverse-variance pooling over the last ``pool_instances`` fits.

    Returns ``{family: {weight, se, dollars_per_window}}`` for each family that
    appears in any eligible fit, or None when no fits carry family weights.
    """
    from . import config as _config

    if cfg is None:
        cfg = {}
    pool_n = _config.get(cfg, "fit.pool_instances", 3)
    eligible = [f for f in fits if f.get("slope") and f["slope"] > 0]
    eligible = eligible[-pool_n:]
    if not eligible:
        return None
    # Collect per-family data from the fits that carry families
    fam_data = {}  # family -> [(weight, se)]
    for f in eligible:
        fams = f.get("families")
        if isinstance(fams, dict):
            for fam, info in fams.items():
                w = info.get("weight")
                s = info.get("se")
                if w and w > 0:
                    fam_data.setdefault(fam, []).append((w, s))
    if not fam_data:
        return None
    out = {}
    for fam, pairs in fam_data.items():
        have_se = all(s and s > 0 for _, s in pairs)
        if have_se and len(pairs) >= 1:
            ws = [1.0 / (s ** 2) for _, s in pairs]
            sw = sum(ws)
            weight = sum(iw * w for iw, (w, _s) in zip(ws, pairs)) / sw
            se = (1.0 / sw) ** 0.5
        else:
            weight = sum(w for w, _ in pairs) / len(pairs)
            se = None
        dpw = (1.0 / weight) if weight > 0 else None
        out[fam] = {"weight": weight, "se": se, "dollars_per_window": dpw}
    return out


# --------------------------------------------------------------------------- pct_saved

def pct_saved(saved_usd, slope_or_weights):
    """Percent of window saved.

    Old signature: ``pct_saved(float, float)`` = saved_usd * slope * 100.
    New signature: ``pct_saved({family: usd}, {family: {weight, ...}})``
    = Σ saved_f × w_f × 100 across families.  A family without a weight in the
    dict uses the pooled single slope (caller should fall back to ``pooled_slope``
    when weights are unavailable).
    """
    if saved_usd is None or slope_or_weights is None:
        return None
    # New path: dict × dict
    if isinstance(saved_usd, dict) and isinstance(slope_or_weights, dict):
        total = 0.0
        for fam, usd in saved_usd.items():
            info = slope_or_weights.get(fam)
            if info is None:
                continue
            w = info.get("weight") if isinstance(info, dict) else info
            if w is None:
                continue
            try:
                total += float(usd) * float(w) * 100.0
            except (TypeError, ValueError):
                pass
        return total if total != 0.0 or any(slope_or_weights.values()) else None
    # Old path: float × float
    try:
        return float(saved_usd) * float(slope_or_weights) * 100.0
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- cost_axis (DB)

def _parse_ts_epoch(text):
    """Parse ISO-8601 UTC string to epoch seconds (no datetime import)."""
    # Format: YYYY-MM-DDTHH:MM:SSZ or YYYY-MM-DDTHH:MM:SS.fffZ
    if not text:
        return None
    s = str(text)
    if s.endswith("Z") or s.endswith("z"):
        s = s[:-1]
    date_part, _, clock = s.partition("T")
    if not clock:
        date_part, _, clock = s.partition(" ")
    ymd = date_part.split("-")
    if len(ymd) != 3:
        return None
    try:
        year, month, day = int(ymd[0]), int(ymd[1]), int(ymd[2])
    except ValueError:
        return None
    hours = minutes = 0
    seconds = 0.0
    if clock:
        cp = clock.split(":")
        try:
            hours = int(cp[0])
            if len(cp) > 1:
                minutes = int(cp[1])
            if len(cp) > 2:
                seconds = float(cp[2])
        except ValueError:
            pass
    # days from civil date (Howard Hinnant algorithm, same as statusline.py)
    year -= 1 if month <= 2 else 0
    era = (year if year >= 0 else year - 399) // 400
    yoe = year - era * 400
    doy = (153 * (month + (-3 if month > 2 else 9)) + 2) // 5 + day - 1
    doe = yoe * 365 + yoe // 4 - yoe // 100 + doy
    days = era * 146097 + doe - 719468
    return days * 86400 + hours * 3600 + minutes * 60 + seconds


def _model_filter_key(window):
    """Extract a model filter substring from a model-scoped window key.

    A window key that is neither five_hour nor seven_day is model-scoped.
    The filter = the key's lowercase text without digits/underscores.
    Today only 'fable' exists; kept generic.
    """
    if window in ("five_hour", "seven_day"):
        return None
    # model_scoped:Fable -> fable;  seven_day_opus -> opus
    key = str(window).lower()
    # strip "model_scoped:" prefix if present
    if ":" in key:
        key = key.split(":", 1)[1]
    # remove digits and underscores
    return "".join(c for c in key if c.isalpha()) or None


def cost_axis(conn, account, started_epoch, epochs, model_filter=None,
              readers=None):
    """Cumulative cost at each sample epoch from ``turns``.

    Returns a list of costs, one per epoch in ``epochs``, ordered the same way.
    Each cost is the sum of turns.cost_usd for kind='api' rows of this account
    whose ts >= started and ts <= the sample epoch's ISO equivalent.
    ``model_filter`` = a substring the normalized model must contain.
    Residual rows (kind != 'api') are never part of it.

    ``readers`` (optional): a list of ``db.union_readers`` dicts; when given,
    turns from every reader whose account matches are merged, deduped by
    ``msg_id``.  ``conn`` is always included (the local reader).

    Billing note (T30.c2): the harness bills a ``[1m]`` launch model at the
    long-context premium (``prices.LONG_CONTEXT_MULT``, pa/prices.py:47-48) on
    every turn, inflating a router session's harness cost by about a third.
    This axis is the ledger's own per-turn pricing (``turns.cost_usd``, base
    rates; transcript.py never passes ``long_context``), so the fit is unbiased
    by it; ``sessions.harness_cost_usd`` (T30.c3) is only a cross-check.
    """
    import bisect

    if not epochs:
        return []

    # started as ISO for the SQL filter
    import time as _time
    started_iso = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(started_epoch))

    # Collect (ts_str, cost, msg_id) from conn + readers, dedup by msg_id
    seen_ids = set()
    all_rows = []

    def _fetch(c, default_account=None):
        acct = account
        if model_filter:
            rows = c.execute(
                "SELECT ts, cost_usd, model, msg_id FROM turns "
                "WHERE kind='api' AND COALESCE(account, ?) IS ? AND ts>=? ORDER BY ts",
                (default_account, acct, started_iso)).fetchall()
            from . import prices as _prices
            for r in rows:
                mid = r[3] if isinstance(r, (list, tuple)) else r["msg_id"]
                if mid in seen_ids:
                    continue
                nm = _prices.normalize_model(r[2] if isinstance(r, (list, tuple)) else r["model"])
                if model_filter in nm:
                    seen_ids.add(mid)
                    all_rows.append(r)
        else:
            rows = c.execute(
                "SELECT ts, cost_usd, msg_id FROM turns "
                "WHERE kind='api' AND COALESCE(account, ?) IS ? AND ts>=? ORDER BY ts",
                (default_account, acct, started_iso)).fetchall()
            for r in rows:
                mid = r[2] if isinstance(r, (list, tuple)) else r["msg_id"]
                if mid in seen_ids:
                    continue
                seen_ids.add(mid)
                all_rows.append(r)

    _fetch(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _fetch(rc, default_account=rd.get("account"))

    # Sort by ts
    def _ts_key(r):
        ts_str = r[0] if isinstance(r, (list, tuple)) else r["ts"]
        ep = _parse_ts_epoch(ts_str)
        return ep if ep is not None else 0
    all_rows.sort(key=_ts_key)

    # Build a cumulative cost array with corresponding epoch timestamps
    cum_ts = []
    cum_cost = []
    running = 0.0
    for r in all_rows:
        ts_str = r[0] if isinstance(r, (list, tuple)) else r["ts"]
        cost = float(r[1] if isinstance(r, (list, tuple)) else r["cost_usd"])
        ep = _parse_ts_epoch(ts_str)
        if ep is None:
            continue
        running += cost
        cum_ts.append(ep)
        cum_cost.append(running)

    # For each sample epoch, find cumulative cost up to that epoch
    result = []
    for ep in epochs:
        if not cum_ts:
            result.append(0.0)
            continue
        idx = bisect.bisect_right(cum_ts, ep)
        if idx == 0:
            result.append(0.0)
        else:
            result.append(cum_cost[idx - 1])
    return result


def cost_axes(conn, account, started_epoch, epochs, readers=None):
    """Per-sample cumulative cost broken out by family.

    Returns a list of dicts ``{family: cumulative_cost}`` one per epoch.
    One query, the same union/dedupe as :func:`cost_axis`; ``cost_axis`` is
    kept as the sum for callers that need a single number.
    """
    import bisect
    from . import prices as _prices

    if not epochs:
        return []

    import time as _time
    started_iso = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime(started_epoch))

    seen_ids = set()
    all_rows = []  # (ts_str, cost, family, msg_id)

    def _fetch(c, default_account=None):
        acct = account
        rows = c.execute(
            "SELECT ts, cost_usd, model, msg_id FROM turns "
            "WHERE kind='api' AND COALESCE(account, ?) IS ? AND ts>=? ORDER BY ts",
            (default_account, acct, started_iso)).fetchall()
        for r in rows:
            mid = r[3] if isinstance(r, (list, tuple)) else r["msg_id"]
            if mid in seen_ids:
                continue
            seen_ids.add(mid)
            mdl = r[2] if isinstance(r, (list, tuple)) else r["model"]
            fam = family_of(mdl)
            ts_str = r[0] if isinstance(r, (list, tuple)) else r["ts"]
            cost = float(r[1] if isinstance(r, (list, tuple)) else r["cost_usd"])
            all_rows.append((ts_str, cost, fam, mid))

    _fetch(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _fetch(rc, default_account=rd.get("account"))

    # Sort by ts
    def _ts_key(r):
        ep = _parse_ts_epoch(r[0])
        return ep if ep is not None else 0
    all_rows.sort(key=_ts_key)

    # Build per-family cumulative cost arrays with epoch timestamps
    cum_ts = []
    running = {}  # family -> running total
    cum_by_fam = []  # list of {family: cost} at each turn

    for ts_str, cost, fam, mid in all_rows:
        ep = _parse_ts_epoch(ts_str)
        if ep is None:
            continue
        running[fam] = running.get(fam, 0.0) + cost
        cum_ts.append(ep)
        cum_by_fam.append(dict(running))

    # For each sample epoch, find cumulative per-family cost
    result = []
    for ep in epochs:
        if not cum_ts:
            result.append({})
            continue
        idx = bisect.bisect_right(cum_ts, ep)
        if idx == 0:
            result.append({})
        else:
            result.append(dict(cum_by_fam[idx - 1]))
    return result


# --------------------------------------------------------------------------- instance helpers (DB)

def instance_samples(conn, account, window, resets_at, readers=None):
    """[(epoch, pct)] from ``utilization`` for one instance.

    ``readers``: optional ``db.union_readers`` list; samples from every reader
    for the same (account, window, resets_at) are merged, deduped by
    ``(session_id, ts, window)`` so two machines sampling the same meter
    instant contribute one point.
    """
    seen = set()  # (session_id, ts, window)
    out = []

    def _collect(c, default_account=None):
        rows = c.execute(
            "SELECT ts, pct, session_id FROM utilization "
            "WHERE COALESCE(account, ?) IS ? AND window=? AND resets_at=? ORDER BY ts",
            (default_account, account, window, resets_at)).fetchall()
        for r in rows:
            ts_str = r[0] if isinstance(r, (list, tuple)) else r["ts"]
            sid = r[2] if isinstance(r, (list, tuple)) else r["session_id"]
            key = (sid, ts_str, window)
            if key in seen:
                continue
            seen.add(key)
            pct_val = float(r[1] if isinstance(r, (list, tuple)) else r["pct"])
            ep = _parse_ts_epoch(ts_str)
            if ep is not None:
                out.append((ep, pct_val))

    _collect(conn)
    for rd in (readers or []):
        rc = rd.get("conn")
        if rc is None or rc is conn:
            continue
        _collect(rc, default_account=rd.get("account"))

    out.sort(key=lambda x: x[0])
    return out


def instance_is_young(conn, cfg, account, window, resets_at, started_epoch, now=None):
    """(young, reason): age under ``fit.instance_min_age_s``, fewer
    ``utilization`` samples than ``fit.instance_min_samples`` (T30.c2), or a pct
    span (max-min) under ``fit.instance_min_span_pct`` (T32.1).

    A young instance's own fit is too noisy to price the window; the caller
    lets the previous instance's fit stand in.  reason is None when not young.
    """
    import time as _time
    from . import config as _config

    min_age = _config.get(cfg, "fit.instance_min_age_s", 86400)
    min_n = _config.get(cfg, "fit.instance_min_samples", 50)  # was 36
    min_span = _config.get(cfg, "fit.instance_min_span_pct", 10)  # T32.1
    if now is None:
        now = _time.time()
    if started_epoch is not None and min_age:
        age = now - started_epoch
        if age < min_age:
            return True, "age %.1fh < %gh" % (age / 3600.0, min_age / 3600.0)
    if min_n or min_span:
        n, lo, hi = conn.execute(
            "SELECT COUNT(*), MIN(pct), MAX(pct) FROM utilization WHERE account IS ? "
            "AND window=? AND resets_at IS ?", (account, window, resets_at)).fetchone()
        if min_n and n < min_n:
            return True, "samples %d < %d" % (n, min_n)
        span = (float(hi) - float(lo)) if n else 0.0
        if min_span and span < min_span:
            return True, "span %.1f < %g" % (span, min_span)
    return False, None


def previous_instance_fit(conn, account, window, resets_at):
    """The latest earlier fitted instance (same account+window, resets_at < this,
    pct_per_dollar set, quality != 'none') as the accounts-block fit shape, or None.
    """
    import json as _json

    r = conn.execute(
        "SELECT pct_per_dollar, fit_se, fit_n, fit_crossings, fit_span, fit_method, "
        "fit_quality, fit_detail, resets_at FROM window_instances "
        "WHERE account IS ? AND window=? AND resets_at < ? "
        "AND pct_per_dollar IS NOT NULL AND COALESCE(fit_quality, 'none') != 'none' "
        "ORDER BY resets_at DESC LIMIT 1", (account, window, resets_at)).fetchone()
    if not r:
        return None
    fams = None
    if r[7]:
        try:
            fams = _json.loads(r[7])
        except (ValueError, TypeError):
            fams = None
    return {"slope": r[0], "se": r[1], "n": r[2], "crossings": r[3],
            "span_pct": r[4], "method": r[5], "quality": r[6],
            "families": fams, "resets_at": r[8]}


def refit_instance(conn, cfg, account, window, resets_at, readers=None):
    """Fit one instance and upsert the result.

    Returns the fit dict.  ``readers``: optional ``db.union_readers`` list
    forwarded to :func:`instance_samples` and :func:`cost_axes`.
    """
    import json as _json
    from . import db, config as _config, summary as _summary

    samples_2d = instance_samples(conn, account, window, resets_at,
                                  readers=readers)
    if not samples_2d:
        return {"method": None, "slope": None, "quality": "none", "families": None}

    # Determine started_at from the window_instances row, else resets_at - duration
    inst_row = conn.execute(
        "SELECT started_at FROM window_instances "
        "WHERE account IS ? AND window=? AND resets_at=?",
        (account, window, resets_at)).fetchone()
    if inst_row and inst_row[0]:
        started = int(inst_row[0])
    else:
        dur = _summary.window_duration(window, cfg)
        started = int(resets_at) - dur

    # Determine model filter for model-scoped windows
    mf = _model_filter_key(window)

    sample_epochs = [s[0] for s in samples_2d]

    if mf:
        # model-scoped windows use the single cost axis
        costs = cost_axis(conn, account, started, sample_epochs, model_filter=mf,
                          readers=readers)
        samples_3d = [(samples_2d[i][0], samples_2d[i][1], costs[i])
                      for i in range(len(samples_2d))]
    else:
        # per-family cost axes for non-model-scoped windows
        fam_costs = cost_axes(conn, account, started, sample_epochs,
                              readers=readers)
        samples_3d = [(samples_2d[i][0], samples_2d[i][1], fam_costs[i])
                      for i in range(len(samples_2d))]

    fit_result = fit_instance(samples_3d, cfg)

    # Build fit_detail JSON
    fit_detail = None
    fams = fit_result.get("families")
    if fams:
        fit_detail = _json.dumps(fams, ensure_ascii=False, default=str)

    # pct_per_dollar: Fable weight when present, else dominant family's weight
    ppd = fit_result.get("slope")
    if fams:
        if "fable" in fams:
            ppd = fams["fable"].get("weight", ppd)
        else:
            best = max(fams, key=lambda f: fams[f].get("weight", 0))
            ppd = fams[best].get("weight", ppd)

    # Upsert the fit columns into window_instances
    row = {"account": account, "window": window, "resets_at": int(resets_at),
           "quantized": 1 if fit_result.get("quantized") else (0 if fit_result.get("quantized") is False else None),
           "pct_per_dollar": ppd,
           "fit_method": fit_result.get("method"),
           "fit_n": fit_result.get("n"),
           "fit_crossings": fit_result.get("crossings"),
           "fit_span": fit_result.get("span_pct"),
           "fit_se": fit_result.get("se"),
           "fit_quality": fit_result.get("quality"),
           "fit_detail": fit_detail}
    db.upsert_window_instance(conn, row)
    return fit_result


# --------------------------------------------------------------------------- pooled family fit (T32.1)

POOL_WINDOWS = ("five_hour", "seven_day")


def build_pool(conn, cfg, readers=None):
    """Per-family fit pooled across same-tier accounts, regime-filtered, into ``fit_pool``.

    Only instances of :data:`POOL_WINDOWS` whose account has a regime boundary
    (``config.regime_since_for``), that started at or after it, and that carry
    ≥ ``fit.pool_min_instance_samples`` samples contribute; no boundary, no pooling.
    Within each instance pct/100 and every family's cumulative cost are demeaned
    (instance fixed effects), then one weighted OLS per (tier, window) over the
    families whose pooled max cumulative cost ≥ ``fit.family_min_usd``; families
    with weight ≤ 0 are dropped and the fit redone, repeatedly until every weight > 0
    or no family remains (T33; :func:`_fit_multi` still refits once).
    Returns ``{(tier, window): {family: {rate, se, n}}}``.
    """
    import time as _time
    from . import db, config as _config, summary as _summary

    cfg = cfg or {}
    min_samples = int(_config.get(cfg, "fit.pool_min_instance_samples", 8) or 8)
    fam_min = float(_config.get(cfg, "fit.family_min_usd", 5) or 0)
    built_at = _time.strftime("%Y-%m-%dT%H:%M:%SZ", _time.gmtime())
    marks = ",".join("?" * len(POOL_WINDOWS))
    try:
        rows = conn.execute(
            "SELECT account, window, resets_at, started_at FROM window_instances "
            "WHERE window IN (%s) ORDER BY account, window, resets_at" % marks,
            POOL_WINDOWS).fetchall()
        conn.execute("DELETE FROM fit_pool WHERE window IN (%s)" % marks, POOL_WINDOWS)
    except Exception:
        return {}   # a pre-v6 ledger: no fit_pool table yet

    groups = {}   # (tier, window) -> {"inst": [samples_3d, ...], "since": [(epoch, iso)]}
    for r in rows:
        acct, win, ra, st = r[0], r[1], r[2], r[3]
        since = _config.regime_since_for(cfg, acct)
        since_ep = _parse_ts_epoch(since) if since else None
        if since_ep is None:
            continue   # never pool blindly
        started = int(st) if st else int(ra) - _summary.window_duration(win, cfg)
        if started < since_ep:
            continue   # another usage-limit regime
        samples_2d = instance_samples(conn, acct, win, ra, readers=readers)
        if len(samples_2d) < min_samples:
            continue
        fam_costs = cost_axes(conn, acct, started, [s[0] for s in samples_2d], readers=readers)
        samples_3d = [(samples_2d[i][0], samples_2d[i][1], fam_costs[i])
                      for i in range(len(samples_2d))]
        g = groups.setdefault((_config.tier_for(cfg, acct), win), {"inst": [], "since": []})
        g["inst"].append(samples_3d)
        g["since"].append((since_ep, since))

    out = {}
    for key in sorted(groups):
        tier, win = key
        insts = groups[key]["inst"]
        fams = [f for f in FAMILIES
                if max((s[2].get(f, 0.0) for inst in insts for s in inst), default=0.0) >= fam_min]
        if not fams:
            continue
        ys, raw = [], []   # demeaned pct/100; per-sample demeaned cost rows over FAMILIES order
        for inst in insts:
            m = float(len(inst))
            my = sum(s[1] for s in inst) / 100.0 / m
            mc = {f: sum(s[2].get(f, 0.0) for s in inst) / m for f in fams}
            for s in inst:
                ys.append(s[1] / 100.0 - my)
                raw.append({f: s[2].get(f, 0.0) - mc[f] for f in fams})
        ws = [1.0] * len(ys)
        mfit = _weighted_ols_multi([[x[f] for f in fams] for x in raw], ys, ws)
        if mfit is None:
            continue
        _, slopes, ses = mfit
        for _ in range(len(fams)):   # T33: drop and refit until every slope > 0
            if not any(s <= 0 for s in slopes):
                break
            fams = [f for i, f in enumerate(fams) if slopes[i] > 0]
            if not fams:
                break
            mfit = _weighted_ols_multi([[x[f] for f in fams] for x in raw], ys, ws)
            if mfit is None:
                break
            _, slopes, ses = mfit
        if not fams or mfit is None or any(s <= 0 for s in slopes):
            continue
        since_iso = min(groups[key]["since"])[1]
        res = {}
        for i, f in enumerate(fams):
            res[f] = {"rate": slopes[i], "se": ses[i], "n": len(ys)}
            db.upsert_fit_pool(conn, {"tier": tier, "window": win, "family": f, "rate": slopes[i],
                                      "se": ses[i], "n": len(ys), "n_instances": len(insts),
                                      "regime_since": since_iso, "built_at": built_at})
        out[key] = res
    return out


def pool_rates(conn, tier, window, cfg=None):
    """``{family: {weight, se, n, dollars_per_window}}`` from ``fit_pool``, or None.

    fix-20: at ``seven_day`` (``fit.borrow_rates``), a family with a ``five_hour``
    rate but no weekly one borrows ``rate_5h × median(rate_7d / rate_5h)`` over the
    families fitted at both windows; such entries carry ``borrowed_from: "five_hour"``.
    """
    try:
        rows = conn.execute("SELECT family, rate, se, n FROM fit_pool WHERE tier=? AND window=?",
                            (tier, window)).fetchall()
    except Exception:
        return None
    out = {}
    for r in rows:
        w = r[1]
        out[r[0]] = {"weight": w, "se": r[2], "n": r[3],
                     "dollars_per_window": (1.0 / w) if w and w > 0 else None}
    if out and window == "seven_day":
        from . import config as _config
        if _config.get(cfg if cfg is not None else _config.load(), "fit.borrow_rates", True):
            try:
                five = {r[0]: (r[1], r[2]) for r in conn.execute(
                    "SELECT family, rate, n FROM fit_pool WHERE tier=? AND window='five_hour'",
                    (tier,)).fetchall()}
            except Exception:
                five = {}
            ratios = sorted(out[f]["weight"] / five[f][0] for f in out
                            if f in five and five[f][0] and five[f][0] > 0
                            and out[f]["weight"] and out[f]["weight"] > 0)
            if ratios:
                m = len(ratios) // 2
                med = ratios[m] if len(ratios) % 2 else (ratios[m - 1] + ratios[m]) / 2.0
                for f, (r5, n5) in five.items():
                    if f in out or not r5 or r5 <= 0:
                        continue
                    w = r5 * med
                    out[f] = {"weight": w, "se": None, "n": n5,
                              "dollars_per_window": 1.0 / w, "borrowed_from": "five_hour"}
    if not out:
        return None
    return {f: out[f] for f in sorted(out, key=lambda f: (FAMILIES.index(f) if f in FAMILIES
                                                          else len(FAMILIES), f))}


def pool_meta(conn, tier, window):
    """``{n_instances, regime_since, built_at}`` for one (tier, window) pool, or None."""
    try:
        r = conn.execute("SELECT MAX(n_instances), MIN(regime_since), MAX(built_at) FROM fit_pool "
                         "WHERE tier=? AND window=?", (tier, window)).fetchone()
    except Exception:
        return None
    if r is None or r[0] is None:
        return None
    return {"n_instances": r[0], "regime_since": r[1], "built_at": r[2]}


def refit_all(conn, cfg, account=None, readers=None):
    """Refit every window instance (optionally filtered by account).

    ``readers``: optional ``db.union_readers`` list forwarded to each
    :func:`refit_instance` call so the fit sees all machines' turns/samples.
    """
    if account:
        rows = conn.execute(
            "SELECT account, window, resets_at FROM window_instances "
            "WHERE account IS ? ORDER BY resets_at", (account,)).fetchall()
    else:
        rows = conn.execute(
            "SELECT account, window, resets_at FROM window_instances "
            "ORDER BY account, window, resets_at").fetchall()
    fits = {}
    for r in rows:
        acct = r[0] if isinstance(r, (list, tuple)) else r["account"]
        win = r[1] if isinstance(r, (list, tuple)) else r["window"]
        ra = r[2] if isinstance(r, (list, tuple)) else r["resets_at"]
        fit = refit_instance(conn, cfg, acct, win, ra, readers=readers)
        fits[(acct, win, ra)] = fit
    build_pool(conn, cfg, readers=readers)   # T32.1: the regime-filtered per-family pool
    try:
        conn.commit()
    except Exception:
        pass
    return fits
