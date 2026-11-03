"""Exact finite-horizon upper bound on Y(N*DT), any strategy of both organisms.

C enters the medium only through organism 2 (A + B -> C); organism 1 makes Y 1:1 from C.
Balance: Y_N = sum_{k<N} DT B2_k d_k - C_N. In the last step organism 1's C uptake is
capped by C_{N-1} (depletion term), so C_N >= DT B2_{N-1} d_{N-1}, giving
    Y_N <= sum_{k<=N-2} DT B2_k d_k.
B is never produced, so B_k <= 10 and d_k <= VMAX B_k/(KM+B_k) <= r := VMAX*10/10.05;
B2_{k+1} = B2_k exp(0.02 DT d_k) <= B2_k exp(0.02 DT r). Combined with the stoichiometric
yield bound (5 mM):  Y_N <= min(5, sum_{k<=N-2} DT r BIO0 exp(0.02 DT r k)).
Evaluated with exact rationals for the sum's coefficients and mpmath exp (50 digits).
"""
import mpmath as mp
from model import VMAX, KM, DT, BIO0

mp.mp.dps = 50


def ub(N):
    r = mp.mpf(VMAX) * 10 / (mp.mpf(10) + mp.mpf(KM))
    s = sum(mp.mpf(DT) * r * mp.mpf(BIO0) * mp.e ** (mp.mpf("0.02") * mp.mpf(DT) * r * k) for k in range(N - 1))
    return min(mp.mpf(5), s)


if __name__ == "__main__":
    for T in [1, 2, 3, 4, 5, 6, 7, 8, 10]:
        print(T, mp.nstr(ub(int(round(T / DT))), 6))
