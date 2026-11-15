"""Hard gates before area comparison. No weighted proxy score."""
import math


def assess(proof, synthesis, fingerprint, depth_limit, best):
    area, depth = synthesis.get('area'), synthesis.get('logic_depth')
    complete = (type(area) in (float, int) and math.isfinite(area) and area >= 0
                and type(depth) is int and depth >= 0 and synthesis.get('timing_metric') == 'logic_depth')
    config_matches = bool(fingerprint) and synthesis.get('config_fingerprint') == fingerprint
    timing_pass = complete and (depth_limit is None or depth <= depth_limit)
    eligible = proof.get('status') == 'PASS' and synthesis.get('status') == 'OK' and complete and config_matches and timing_pass
    accepted = eligible and (best is None or area < best['area'])
    if proof.get('status') != 'PASS':
        reason = 'EQUIVALENCE_NOT_PASS'
    elif synthesis.get('status') != 'OK':
        reason = 'SYNTHESIS_' + synthesis.get('status', 'ERROR')
    elif not complete:
        reason = 'INCOMPLETE_METRICS'
    elif not config_matches:
        reason = 'CONFIG_MISMATCH'
    elif not timing_pass:
        reason = 'DEPTH_LIMIT'
    elif not accepted:
        reason = 'NO_AREA_IMPROVEMENT'
    else:
        reason = 'ACCEPTED'
    return {'metrics_complete': complete, 'config_matches': config_matches,
            'timing_pass': timing_pass, 'eligible': eligible, 'accepted': accepted, 'reason': reason}
