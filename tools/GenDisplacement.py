#
# GenDisplacement.py
#
# Class to generate displacements of atoms
#
# Copyright (c) 2020 Terumasa Tadano
#
# This file is distributed under the terms of the MIT license.
# Please see the file 'LICENCE.txt' in the root directory
# or http://opensource.org/licenses/mit-license.php for information.
#
from __future__ import print_function
import numpy as np
import random
import copy
import math
import cmath


class AlamodeDisplace(object):

    def __init__(self, displacement_mode, codeobj_base,
                 file_evec=None,
                 file_primitive=None):
        self._pattern = []
        self._primitive_lattice_vector = None
        self._inverse_primitive_lattice_vector = None
        self._elements = None
        self._nat_primitive = 0
        self._xp_fractional = None
        self._counter = 1
        self._displacement_magnitude = 0.02
        self._md_snapshots = None  # displacements in angstrom unit
        self._verbosity = 1
        self._classical = False
        self._commensurate_qpoints = []
        self._mapping_shift = None
        self._mapping_s2p = None
        self._qpoints = None
        self._omega2 = None
        self._evec = None
        self._qlist_real = []
        self._qlist_uniq = []
        self._mass = None
        self._nmode = None

        self._displacement_mode = displacement_mode.lower()
        self._supercell = codeobj_base

        self._BOHR_TO_ANGSTROM = 0.5291772108
        self._K_BOLTZMANN = 1.3806488e-23
        self._RYDBERG_TO_JOULE = 4.35974394e-18 / 2.0
        amu = 1.660538782e-27
        electron_mass = 9.10938215e-31
        self._AMU_RYD = amu / electron_mass / 2.0

        if file_primitive:
            primitive_cell = copy.deepcopy(codeobj_base)
            primitive_cell.load_initial_structure(file_primitive)

            self._primitive_lattice_vector = primitive_cell._lattice_vector
            self._inverse_primitive_lattice_vector = primitive_cell._inverse_lattice_vector
            self._xp_fractional = primitive_cell.x_fractional
            self._nat_primitive = primitive_cell.nat
            self._find_commensurate_q()
            self._generate_mapping_s2p()

        else:
            if self._displacement_mode == "random_normalcoordinate" \
                    or self._displacement_mode == "pes":
                raise RuntimeError("The --prim option is necessary when '--random --temperature' "
                                   "options are used at the same time or '--pes' is invoked.")

        if file_evec:

            self._load_phonon_results(file_evec)

            #print(self._omega2)

        else:

            if self._displacement_mode == "pes":
                raise RuntimeError("The --evec option is necessary when '--pes' is invoked.")

            if self._displacement_mode == "random_normalcoordinate":

                print("The --evec option is necessary when '--random --temperature'\n"
                      "options are used at the same time. \n"
                      "Please generate a PREFIX.evec file by using the ANPHON code\n"
                      "with the following inputs and then run displace.py again with\n"
                      "--evec=PREFIX.evec option:\n\n")

                print("&kpoint")
                print("0")
                for elem in self._commensurate_qpoints:
                    print("%20.15f %20.15f %20.15f" % (elem[0], elem[1], elem[2]))
                print("/")
                print("&analysis")
                print(" PRINTEVEC = 1")
                print("/")
                exit(0)

    def generate(self, file_pattern=None,
                 file_mddata=None,
                 option_every=None,
                 magnitude=0.00,
                 number_of_displacements=1,
                 temperature=None):

        self._counter = 1
        self._displacement_magnitude = magnitude

        header_list = []
        disp_list = []

        if self._displacement_mode == "md":

            list_every = self._sample_md_snapshots(file_mddata, option_every)

            if self._verbosity > 0:
                print(" The --load_mddata option is given:")
                print(" Sampling range and interval: [%d:%d], interval = %d"
                      % (list_every[0] + 1, list_every[1], list_every[2]))
                print(" %d snapshots are sampled from the LOAD_MDDATA file(s)" % len(self._md_snapshots))
                print("")

            ndisp = len(self._md_snapshots)
            disp_random = self._get_random_displacements(ndisp, "gauss")

            for i in range(ndisp):
                header = "Random disp. with mag %f on top of sampled snapshots: %i" \
                         % (self._displacement_magnitude, self._counter)
                disp_tmp = self._md_snapshots[i]

                # Convert disp_tmp in fractional coordinates
                for j in range(self._supercell.nat):
                    disp_tmp[j] = np.dot(disp_tmp[j],
                                         self._supercell.inverse_lattice_vector.transpose())

                disp_tmp += disp_random[i]
                header_list.append(header)
                disp_list.append(disp_tmp)
                self._counter += 1

            return header_list, disp_list

        if self._displacement_mode == "fd":

            if not file_pattern:
                raise RuntimeError("pattern file must be given with --pattern option")
            self._parse_displacement_patterns(file_pattern)

            for pattern in self._pattern:
                header, disp = self._get_finite_displacement(pattern)
                self._counter += 1
                header_list.append(header)
                disp_list.append(disp)

            return header_list, disp_list

        if self._displacement_mode == "random":
            disp_random = self._get_random_displacements(number_of_displacements,
                                                         "gauss")
            for i in range(number_of_displacements):
                header = "Random disp. with mag %f : %i" % (self._displacement_magnitude,
                                                            self._counter)
                header_list.append(header)
                disp_list.append(disp_random[i])
                self._counter += 1

            return header_list, disp_list

        return header_list, disp_list

    def _sample_md_snapshots(self, file_mddata, str_every):

        disp_merged = []

        try:
            # Get displacements in angstrom unit
            disp_merged = self._supercell.get_displacements(file_mddata, "angstrom")
        except:
            try:
                for target in file_mddata:
                    disp = np.loadtxt(target, dtype=np.float)
                    disp *= self._BOHR_TO_ANGSTROM
                    disp_merged.extend(np.reshape(disp, (len(disp) // self._supercell.nat, self._supercell.nat, 3)))
            except:
                raise RuntimeError("Failed to read the MD files")

        list_str_every = str_every.strip().split(':')
        start = 0
        end = len(disp_merged)
        if len(list_str_every) == 1:
            interval = int(list_str_every[0])
        elif len(list_str_every) == 3:
            start = int(list_str_every[0]) - 1
            end = int(list_str_every[1])
            interval = int(list_str_every[2])

            if start > end:
                raise RuntimeError("In the --every option, start must not be larger than end.")

            if start > len(disp_merged) or end > len(disp_merged):
                raise RuntimeError("The range specified by --every is larger than the loaded MD data.")

        else:
            raise RuntimeError("Invalid format of the --every option.")

        self._md_snapshots = disp_merged[0:1000:10]

        return [start, end, interval]

    def _parse_displacement_patterns(self, files_in):
        self._pattern = []

        for file in files_in:
            pattern_tmp = []

            f = open(file, 'r')
            tmp, basis = f.readline().rstrip().split(':')
            if basis == 'F':
                raise RuntimeError("DBASIS must be 'C'")

            while True:
                line = f.readline()
                if not line:
                    break

                line_split_by_colon = line.rstrip().split(':')
                is_entry = len(line_split_by_colon) == 2

                if is_entry:
                    pattern_set = []
                    natom_move = int(line_split_by_colon[1])
                    for i in range(natom_move):
                        disp = []
                        line = f.readline()
                        line_split = line.rstrip().split()
                        disp.append(int(line_split[0]))
                        for j in range(3):
                            disp.append(float(line_split[j + 1]))

                        pattern_set.append(disp)
                    pattern_tmp.append(pattern_set)

            for entry in pattern_tmp:
                if entry not in self._pattern:
                    self._pattern.append(entry)
            f.close()

    def _get_finite_displacement(self, pattern):

        header = "Disp. Num. %i" % self._counter
        header += " ( %f Angstrom" % self._displacement_magnitude
        disp = np.zeros((self._supercell.nat, 3))

        for displace in pattern:
            atom = displace[0] - 1
            header += ", %i : " % displace[0]
            str_direction = ""

            for i in range(3):
                if abs(displace[i + 1]) > 1.0e-10:
                    if displace[i + 1] > 0.0:
                        str_direction += "+" + self._char_xyz(i)
                    else:
                        str_direction += "-" + self._char_xyz(i)

                disp[atom][i] += displace[i + 1] * self._displacement_magnitude
            header += str_direction
        header += ")"

        if self._supercell.inverse_lattice_vector is not None:
            for i in range(self._supercell.nat):
                disp[i] = np.dot(disp[i], self._supercell.inverse_lattice_vector.T)

        return header, disp

    def _get_random_displacements(self, ndata, mode="gauss"):
        """
        Return random displacements in fractional coordinates
        """
        disp_xyz = np.zeros(3)
        disp_random = np.zeros((ndata, self._supercell.nat, 3))

        if mode == "gauss":
            for idata in range(ndata):
                for i in range(self._supercell.nat):
                    for j in range(3):
                        # Generate a random number following the Gaussian distribution
                        disp_xyz[j] = random.gauss(0.0, 1.0)

                    # Normalize the random displacement so that it has the norm
                    # of self._displacement_magnitude.
                    norm = np.linalg.norm(disp_xyz)
                    disp_random[idata, i, :] = disp_xyz[:] / norm * self._displacement_magnitude

                    # Transform to the fractional coordinate
                    disp_random[idata, i] = np.dot(disp_random[idata, i],
                                                   self._supercell.inverse_lattice_vector.transpose())

        elif mode == "uniform":
            for idata in range(ndata):
                for i in range(self._supercell.nat):
                    for j in range(3):
                        # Generate a random number following the Gaussian distribution
                        disp_xyz[j] = random.uniform(-self._displacement_magnitude,
                                                     self._displacement_magnitude)

                    # Transform to the fractional coordinate
                    disp_random[idata, i] = np.dot(disp_xyz[:],
                                                   self._supercell.inverse_lattice_vector.transpose())
        else:
            raise RuntimeError("Invalid option for the random number distribution types.")

        return disp_random

    def _get_random_displacements_normalcoordinate(self, ndata, temperature):

        nq = len(self._qpoints)
        Q_R = np.zeros((nq, self._nmode, ndata))
        Q_I = np.zeros((nq, self._nmode, ndata))

        sigma = self._get_gaussian_sigma(temperature)

        for iq in range(nq):
            for imode in range(self._nmode):
                if sigma[iq, imode] < 1.0e-10:
                    Q_R[iq, imode, :] = 0.0
                    Q_I[iq, imode, :] = 0.0
                else:
                    Q_R[iq, imode, :] = np.random.normal(
                        0.0, sigma[iq, imode], size=ndata)
                    Q_I[iq, imode, :] = np.random.normal(
                        0.0, sigma[iq, imode], size=ndata)

        disp = np.zeros((self._supercell.nat, 3, ndata))

        for iat in range(self._supercell.nat):
            xshift = self._mapping_shift[iat]
            jat = self._mapping_s2p[iat]
            for iq in self._qlist_real:
                xq_tmp = self._qpoints[iq, :]
                phase_real = math.cos(2.0 * math.pi * np.dot(xq_tmp, xshift))
                for imode in range(self._nmode):
                    for icrd in range(3):
                        disp[iat, icrd, :] += Q_R[iq, imode, :] * \
                                              self._evec[iq, imode, 3 * jat + icrd].real * phase_real

            for iq in self._qlist_uniq:
                xq_tmp = self._qpoints[iq, :]
                phase = cmath.exp(complex(0.0, 1.0) * 2.0 *
                                  math.pi * np.dot(xq_tmp, xshift))
                for imode in range(self._nmode):
                    for icrd in range(3):
                        ctmp = self._evec[iq, imode, 3 * jat + icrd] * phase
                        disp[iat, icrd, :] += math.sqrt(2.0) * (
                                Q_R[iq, imode, :] * ctmp.real - Q_I[iq, imode, :] * ctmp.imag)

        kd = np.array(kd, dtype=int)
        for iat in range(nat):
            factor[iat] = 1.0 / math.sqrt(mass[kd[iat]] * amu_ry * float(nq))

        for idata in range(ndata):
            for i in range(3):
                disp[:, i, idata] = factor[:] * disp[:, i, idata]

        return disp

    def _get_gaussian_sigma(self, temp):

        nq = len(self._qpoints)
        nmode = self._nmode
        omega = np.zeros((nq, nmode))
        sigma = np.zeros((nq, nmode))

        for iq in range(nq):
            for imode in range(nmode):
                if self.omega2[iq, imode] < 0.0:
                    omega[iq, imode] = math.sqrt(-self.omega2[iq, imode])
                else:
                    omega[iq, imode] = math.sqrt(self.omega2[iq, imode])

                if omega[iq, imode] > 1.0e-6:
                    if self._classical:
                        sigma[iq, imode] = math.sqrt(self._n_classical(
                            omega[iq, imode], temp) / omega[iq, imode])
                    else:
                        sigma[iq, imode] = math.sqrt(
                            (1.0 + 2.0 * self._n_bose(omega[iq, imode], temp)) / (2.0 * omega[iq, imode]))

        return sigma

    def _find_commensurate_q(self):

        tol_zero = 1.0e-3

        nqmax = self._supercell.nat // self._nat_primitive
        convertor = np.dot(self._supercell.inverse_lattice_vector,
                           self._primitive_lattice_vector)
        nmax = 10
        qlist = []

        for i in range(3):
            for j in range(3):
                frac = abs(convertor[i, j])

                if frac < tol_zero:
                    convertor[i, j] = 0.0
                else:
                    found_nnp = False
                    for nnp in range(1, 1000):
                        if abs(frac * float(nnp) - 1.0) < tol_zero:
                            found_nnp = True
                            break
                    if found_nnp:
                        convertor[i, j] = np.sign(convertor[i, j]) / float(nnp)
                    else:
                        raise RuntimeError("Failed to express the inverse transformation matrix"
                                           "by using fractional numbers")

        comb = []
        for Lx in range(nmax):
            for Ly in range(nmax):
                for Lz in range(nmax):
                    for sx in (1, -1):
                        for sy in (1, -1):
                            for sz in (1, -1):
                                comb.append([Lx * sx, Ly * sy, Lz * sz])

        for entry in comb:
            vec = np.array([entry[0], entry[1], entry[2]])
            qvec = np.dot(vec, convertor) % 1.0

            for i in range(3):
                if qvec[i] >= 0.5:
                    qvec[i] -= 1.0
                elif qvec[i] < -0.5:
                    qvec[i] += 1.0

            new_entry = True

            for elem in qlist:
                diff = (elem - qvec) % 1.0

                for i in range(3):
                    if diff[i] >= 0.5:
                        diff[i] -= 1.0
                    elif diff[i] < -0.5:
                        diff[i] += 1.0

                norm = math.sqrt(np.dot(diff, diff))

                if norm < tol_zero:
                    new_entry = False
                    break

            if new_entry:
                qlist.append(qvec)

            if len(qlist) == nqmax:
                break

        self._commensurate_qpoints = qlist

    def _generate_mapping_s2p(self):

        tol_zero = 1.0e-3
        convertor = np.dot(self._supercell.lattice_vector.transpose(),
                           self._inverse_primitive_lattice_vector.transpose())

        for i in range(3):
            for j in range(3):
                convertor[i, j] = float(round(convertor[i, j]))

        shift = np.zeros((self._supercell.nat, 3))
        map_s2p = np.zeros(self._supercell.nat, dtype=int)

        for iat in range(self._supercell.nat):
            xtmp = self._supercell.x_fractional[iat, :]
            xnew = np.dot(xtmp, convertor)

            iloc = -1

            for jat in range(self._nat_primitive):
                xp = self._xp_fractional[jat, :]
                xdiff = np.array((xnew - xp) % 1.0)
                for i in range(3):
                    if xdiff[i] >= 0.5:
                        xdiff[i] -= 1.0
                diff = math.sqrt(np.dot(xdiff[:], xdiff[:]))
                if diff < tol_zero:
                    iloc = jat
                    break
            if iloc == -1:
                raise RuntimeError("Equivalent atom not found")

            map_s2p[iat] = iloc
            shift[iat, :] = [float(round(xnew[i] - self._xp_fractional[iloc, i]))
                             for i in range(3)]

        self._mapping_shift = shift
        self._mapping_s2p = map_s2p

    def _n_bose(self, omega, temperature):
        if abs(temperature) < 1.0e-15 or omega < 1.0e-10:
            return 0.0
        else:
            temperature_au = self._K_BOLTZMANN * temperature / self._RYDBERG_TO_JOULE
            x = omega / temperature_au
            return 1.0 / (math.exp(x) - 1.0)

    def _n_classical(self, omega, temperature):
        if abs(temperature) < 1.0e-15 or omega < 1.0e-10:
            return 0.0
        else:
            temperature_au = self._K_BOLTZMANN * temperature / self._RYDBERG_TO_JOULE
            return temperature_au / omega

    def _load_phonon_results(self, file_in):

        tol_zero = 1.0e-3

        f = open(file_in, 'r')

        # skip 10 lines
        for i in range(10):
            f.readline()

        nmode = int(f.readline().split(':')[1])
        nq = int(f.readline().split(':')[1])
        nkd = int(f.readline().split(':')[1])
        mass = [float(t) for t in f.readline().split(':')[1].split()]
        # skip 3 lines
        for i in range(3):
            f.readline()

        omega2 = np.zeros((nq, nmode))

        evec = np.zeros((nq, nmode, nmode), dtype=np.complex128)
        xq = np.zeros((nq, 3))

        for iq in range(nq):
            xq_tmp = [float(a) for a in (f.readline().split(':')[1]).split()]
            xq[iq, :] = xq_tmp[:]
            for imode in range(nmode):
                omega2[iq, imode] = float(f.readline().split(':')[1])
                for jmode in range(nmode):
                    line = f.readline().split()
                    evec[iq, imode, jmode] = complex(
                        float(line[0]), float(line[1]))
                f.readline()
            f.readline()

        qlist_real = []
        qlist_uniq = []

        # Prepare q point list
        for iq in range(nq):
            xq_tmp = xq[iq, :]
            xq_minus = -xq_tmp
            xdiff = (xq_tmp - xq_minus) % 1.0
            norm = math.sqrt(np.dot(xdiff, xdiff))
            if norm < tol_zero:
                qlist_real.append(iq)

            flag_uniq = True

            for jq in qlist_uniq:
                xq_tmp2 = xq[jq, :]
                xdiff = (xq_tmp2 - xq_tmp) % 1.0
                xdiff2 = (xq_tmp2 + xq_tmp) % 1.0
                norm = math.sqrt(np.dot(xdiff, xdiff))
                norm2 = math.sqrt(np.dot(xdiff2, xdiff2))

                if norm < tol_zero or norm2 < tol_zero:
                    flag_uniq = False
                    break
            if flag_uniq:
                qlist_uniq.append(iq)

        qlist_uniq = list(set(qlist_uniq) - set(qlist_real))

        f.close()

        self._qpoints = xq
        self._omega2 = omega2
        self._evec = evec
        self._qlist_real = qlist_real
        self._qlist_uniq = qlist_uniq
        self._mass = mass
        self._nmode = nmode





    def generate_PES_displacements(Q_in, xq, iq, imode, evec, nat, map_s2p, shift, kd, mass):

        Bohr_in_AA = 0.52917721067
        Q_R = Q_in / Bohr_in_AA  # in units of u^{1/2} Bohr

        ndata = len(Q_R)
        nq = len(xq)

        disp = np.zeros((nat, 3, ndata))

        for iat in range(nat):
            xshift = shift[iat, :]
            jat = map_s2p[iat]
            xq_tmp = xq[iq, :]
            phase_real = math.cos(2.0 * math.pi * np.dot(xq_tmp, xshift))
            for icrd in range(3):
                disp[iat, icrd, :] += Q_R[:] * \
                                      evec[iq, imode, 3 * jat + icrd].real * phase_real

        factor = np.zeros(nat)
        for iat in range(nat):
            factor[iat] = 1.0 / math.sqrt(mass[kd[iat]] * float(nq))

        for idata in range(ndata):
            for i in range(3):
                disp[:, i, idata] = factor[:] * disp[:, i, idata]

        return disp

    @staticmethod
    def _char_xyz(entry):
        if entry % 3 == 0:
            return 'x'
        if entry % 3 == 1:
            return 'y'
        if entry % 3 == 2:
            return 'z'
