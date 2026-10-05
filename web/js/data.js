/*
 * Parcel RTK — survey data, transcribed from the recorded documents.
 *
 * Everything here is DATA: bearings and distances exactly as they appear in the
 * documents (US survey feet), with where each came from. model.js decides how the
 * two documents are fitted together; nothing here is computed.
 *
 *   deed : Exhibit "A", legal description of 31652 Shadow Mountain Dr (APN 148669).
 *          The Lodgepole Pines plat shows this tract as "Unplatted, Reception No.
 *          93099507" and quotes its calls as the parenthetical record values.
 *   lp   : Plat of Lodgepole Pines, Reception F0236484, Book 129 Pages 22-23,
 *          Jefferson County, recorded 5/17/1996. Surveyor Noel L. Potter, PLS 26296
 *          (Rea, Cassens & Associates). A field-measured, monumented survey.
 *          Basis of bearing: S line of the SW 1/4 of Sec 5 = N 84°58'14" W (assumed),
 *          between the S 1/4 corner (brass plate in concrete, LS 865) and the
 *          SW corner of the E 1/2 SW 1/4 (2" aluminum cap on #6 rebar, LS 26296).
 */
(function (root, factory) {
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  else (root.PRTK = root.PRTK || {}).data = api;
})(typeof self !== 'undefined' ? self : this, function () {
  'use strict';

  return {
    sources: {
      deed: 'Exhibit "A", legal description, 31652 Shadow Mountain Dr, Conifer CO (APN 148669); record Reception 93099507',
      lp: 'Plat of Lodgepole Pines, Rec. F0236484, Bk 129 Pg 22-23, Jefferson County, 5/17/1996 (N. L. Potter, PLS 26296)'
    },

    // ---- Lodgepole Pines: MEASURED values (survey basis). Record values from the
    //      plat's parentheticals are kept for the consistency report. ----------------
    lp: {
      // POB = S 1/4 corner of Sec 5; first call runs west along the section line.
      southLine: { bearing: 'N 84°58\'14" W', dist: 1322.54 },                 // S1/4 cor -> W1/16 cor
      westLine:  { bearing: 'N 10°27\'36" E', dist: 1040.08,                    // W1/16 cor -> shared corner
                   record: { bearing: 'N 10°27\'03" E', dist: 1040.00 } },
      northLine: { bearing: 'S 76°36\'49" E', dist: 1775.92,                    // shared corner -> road
                   record: { bearing: 'S 76°34\'50" E', dist: 1775.90 } },
      lot1: {
        northDist: 641.00,                                                      // along northLine
        southDist: 641.00,                                                      // along southLine (from W1/16)
        eastLine: { bearing: 'N 10°35\'01" E', dist: 946.69 }                   // Lot 1 / Lot 2 line (a check)
      },
      // W right-of-way of Shadow Mountain Drive, NE corner of the subdivision south to
      // the S 1/4 corner ("the following eight (8) courses").
      row: [
        { type: 'curve', dir: 'L', arc: 104.61, radius: 184.43, delta: '32°29\'53"', chordBearing: 'S 03°33\'32" W', chord: 103.21 },
        { type: 'line',  bearing: 'S 12°41\'24" E', dist: 200.67 },
        { type: 'curve', dir: 'R', arc: 134.92, radius: 106.97, delta: '72°16\'00"', chordBearing: 'S 23°26\'36" W', chord: 126.15 },
        { type: 'line',  bearing: 'S 59°34\'36" W', dist: 144.39 },
        { type: 'curve', dir: 'R', arc: 110.30, radius: 201.37, delta: '31°23\'00"', chordBearing: 'S 75°16\'06" W', chord: 108.93 },
        { type: 'line',  bearing: 'N 89°02\'24" W', dist: 207.23 },
        { type: 'curve', dir: 'L', arc: 183.49, radius: 130.00, delta: '80°52\'08"', chordBearing: 'S 50°31\'32" W', chord: 168.63 },
        { type: 'line',  bearing: 'S 10°05\'28" W', dist: 110.22 }
      ],
      // Record (road-deed) values the plat quotes for some ROW calls; all differ from the
      // measured ones by the same -6'47", i.e. the road record uses another basis.
      rowRecord: [
        { i: 0, chordBearing: 'S 03°40\'08" W' }, { i: 1, bearing: 'S 12°34\'37" E' },
        { i: 3, bearing: 'S 59°41\'23" W' },     { i: 5, bearing: 'N 88°55\'37" W' },
        { i: 7, bearing: 'S 10°12\'15" W' }
      ],
      areasAc: { subdivision: 35.08, lot1: 14.57, lot2: 10.16, lot3: 10.35 }
    },

    // ---- 35-acre tract: RECORD values, in the deed's own basis ----------------------
    deed: {
      // Commencing at the SW corner of the E 1/2 SW 1/4 (= the W1/16 corner) ...
      commence: { bearing: 'N 10°27\'03" E', dist: 1040.00 },                   // ... to the True Point of Beginning
      courses: [
        { type: 'line',  bearing: 'N 10°27\'03" E', dist: 960.00, to: 'P1NW', name: 'west' },
        { type: 'line',  bearing: 'S 69°12\'32" E', dist: 1958.92, to: 'P1NE', name: 'north' },
        { type: 'curve', dir: 'L', arc: 149.26, radius: 415.82, delta: '20°34\'00"', chordBearing: 'S 43°21\'43" W', chord: 148.46, name: 'road' },
        { type: 'line',  bearing: 'S 33°04\'43" W', dist: 38.97, name: 'road' },
        { type: 'curve', dir: 'L', arc: 97.83,  radius: 340.57, delta: '16°04\'50"', name: 'road' },
        { type: 'line',  bearing: 'S 16°59\'53" W', dist: 93.91, name: 'road' },
        { type: 'curve', dir: 'L', arc: 86.02,  radius: 408.46, delta: '12°04\'00"', name: 'road' },
        { type: 'line',  bearing: 'S 04°55\'53" W', dist: 24.74, name: 'road' },
        { type: 'curve', dir: 'R', arc: 121.41, radius: 464.28, delta: '14°59\'00"', name: 'road' },
        { type: 'line',  bearing: 'S 19°54\'53" W', dist: 122.26, to: 'LPNE', name: 'road' },
        { type: 'line',  bearing: 'N 76°34\'50" W', dist: 1775.90, to: 'TPB', name: 'south' }
      ],
      // Errors found in the written description. The courses above stay exactly as
      // written; model.js applies these and reports them.
      errata: [
        { course: 4, field: 'radius', written: 340.57, corrected: 348.57,
          why: 'Arc 97.83 ft with delta 16°04\'50" requires R = 348.57 ft (R x delta = 95.58 ft with 340.57). '
             + 'The delta is confirmed independently: the curve leaves S 33°04\'43" W and arrives tangent to S 16°59\'53" W. '
             + 'With 348.57 the deed closes to 0.01 ft; as written it misses by 2.25 ft. Almost certainly a 0/8 retyping error.' }
      ]
    },

    // ---- Corners and what is physically there --------------------------------------
    // conf: how sure the monument description is ('plat' = read off the plat drawing or
    // notes; 'note2' = implied by plat note 2; 'none' = not described anywhere).
    points: {
      TPB:  { name: 'Shared corner — Lot 1 NW / 35-ac SW (TPB)',
              monument: 'Found #4 rebar (shown on the plat). One monument serves both of your parcels.',
              conf: 'plat', group: 'shared' },
      W16:  { name: 'Lot 1 SW corner (W1/16 corner, Sec 5/8)',
              monument: '2" aluminum cap on #6 rebar stamped "W1/16 S5 S8 1996 LS 26296" (replaced a found #3 rebar). '
                      + 'The plat also shows a 1/16 position from the Black Mountain Ranch Estates plat nearby; the aluminum cap is the one Lodgepole Pines uses.',
              conf: 'plat', group: 'lot1' },
      L1NE: { name: 'Lot 1 NE corner (on the line shared with the 35-ac)',
              monument: '1/2" rebar with 1" plastic cap "LS 26296" (plat note 2: set at all lot corners in 1996).',
              conf: 'note2', group: 'lot1' },
      L1SE: { name: 'Lot 1 SE corner (on the section line)',
              monument: '1/2" rebar with 1" plastic cap "LS 26296" (plat note 2).',
              conf: 'note2', group: 'lot1' },
      LPNE: { name: '35-ac S corner = Lodgepole NE corner (at the road)',
              monument: 'On the west right-of-way of Shadow Mountain Dr. 1/2" rebar with 1" plastic cap "LS 26296" (plat note 2) unless an older monument was found.',
              conf: 'note2', group: 'shared' },
      P1NW: { name: '35-ac NW corner',
              monument: 'Not described in the deed. It sits 960 ft north of the shared corner on the same straight line as Lot 1\'s west line — look for rebar or pipe.',
              conf: 'none', group: 'p1' },
      P1NE: { name: '35-ac NE corner (at the road)',
              monument: 'Not described in the deed. On the west right-of-way of Shadow Mountain Dr.',
              conf: 'none', group: 'p1' },
      S14:  { name: 'S 1/4 corner of Section 5',
              monument: 'Brass plate set in concrete, LS 865 — also a USGS benchmark. Lodgepole Pines point of beginning; very stable control.',
              conf: 'plat', group: 'section' }
      // Points of curve/tangency along the road are generated by model.js
      // (ROW1..ROW7 for Lodgepole — rebar per note 2; P1R1..P1R7 for the 35-ac deed).
    },

    // Other monuments the plat shows on the west line, positions only approximate:
    westLineExtras: 'Between the Lot 1 SW corner and the shared corner the plat also shows a found Hayes & Soucie pin & cap (LS 10388) and found #3 rebars — corners of Black Mountain Ranch Estates Filing 4 on the same line.'
  };
});
