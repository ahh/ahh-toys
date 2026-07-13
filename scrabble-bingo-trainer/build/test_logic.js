// Sanity-check the trainer's word logic against the shipped data.
const fs = require("fs");
const path = require("path");
const dir = path.join(__dirname, "..", "data");
function loadVar(file, name) {
  const src = fs.readFileSync(path.join(dir, file), "utf8");
  const win = {};
  new Function("window", src)(win);
  return win[name];
}
const WORDS7 = loadVar("words7.js", "WORDS7");
const WORDS8 = loadVar("words8.js", "WORDS8");

const BAG = { A:9,B:2,C:2,D:4,E:12,F:2,G:3,H:2,I:9,J:1,K:1,L:4,M:2,N:6,O:8,P:2,Q:1,R:6,S:4,T:6,U:4,V:2,W:2,X:1,Y:2,Z:1 };
function choose(n,k){ if(k<0||k>n)return 0; let r=1; for(let i=0;i<k;i++) r=r*(n-i)/(i+1); return r; }
function alphagram(w){ return w.split("").sort().join(""); }
function drawWeight(a){ const c={}; for(const ch of a) c[ch]=(c[ch]||0)+1; let w=1; for(const ch in c){ w*=choose(BAG[ch.toUpperCase()]||0,c[ch]); if(w===0)return 0;} return w; }

function buildIndex(raw){
  const words = raw.split("\n");
  const set = new Set(words);
  const map = Object.create(null);
  for(const w of words){ const a=alphagram(w); (map[a]||(map[a]=[])).push(w); }
  const alphas = Object.keys(map);
  const weight={}; for(const a of alphas) weight[a]=drawWeight(a);
  alphas.sort((x,y)=>weight[y]-weight[x]);
  return {set,map,ranked:alphas,weight};
}

let fail=0;
function ok(cond,msg){ if(!cond){ console.error("FAIL:",msg); fail++; } }

const I7 = buildIndex(WORDS7);
const I8 = buildIndex(WORDS8);
console.log(`7-letter: ${WORDS7.split("\n").length} words, ${I7.ranked.length} alphagrams`);
console.log(`8-letter: ${WORDS8.split("\n").length} words, ${I8.ranked.length} alphagrams`);

// 1. every word validates against its own alphagram map
for(const w of ["seating","retinas","tisanes","goalies"]) {
  ok(I7.set.has(w), w+" in set");
  ok(I7.map[alphagram(w)].includes(w), w+" in map");
}

// 2. SATINE stem + G = "aeginst" (SEATING/TEASING/...) should rank very common
const satingA = alphagram("seating"); // aeginst
const satingRank = I7.ranked.indexOf(satingA);
ok(satingRank>=0 && satingRank < 250, "SATINE+G ranks very common, got rank "+satingRank);
console.log(satingA+" rank:", satingRank, "->", I7.map[satingA].join(" "));

// 3. top-10 most-probable 7s should all be common-letter words, weight>0
console.log("Top 10 probable 7s:", I7.ranked.slice(0,10).map(a=>I7.map[a][0]).join(" "));
ok(I7.weight[I7.ranked[0]] > 0, "top word drawable");
// words needing blanks (e.g. many rare repeats) sink to weight 0 at the tail
ok(I7.weight[I7.ranked[I7.ranked.length-1]] === 0, "tail includes weight-0 (blank-only) racks");

// 4. RACK MODE: generating from a random alphagram -> letters are a permutation, solution exists
for(let t=0;t<2000;t++){
  const a = I7.ranked[Math.floor(Math.random()*I7.ranked.length)];
  const letters = a.split("");
  ok(alphagram(letters.join(""))===a, "rack letters match alphagram");
  ok(I7.map[a].length>=1, "rack has >=1 solution");
  // the solution is reachable by permuting the tiles
  ok(alphagram(I7.map[a][0])===a, "solution is anagram of rack");
}

// 5. BOARD MODE: pick 8-word, lock one position, rack = other 7. Must be solvable
//    and the removed multiset + locked letter must reconstruct a valid word.
for(let t=0;t<5000;t++){
  const a = I8.ranked[Math.floor(Math.random()*Math.min(3000,I8.ranked.length))];
  const sols = I8.map[a];
  const w = sols[Math.floor(Math.random()*sols.length)];
  const i = Math.floor(Math.random()*8);
  const lock = w[i];
  const rack = (w.slice(0,i)+w.slice(i+1)).split("");
  ok(rack.length===7, "board rack has 7 tiles");
  const reachable = sols.filter(s=>s[i]===lock);
  ok(reachable.includes(w), "the source word is reachable through the locked tile");
  // reconstruct: place rack tiles into the 7 open slots to spell w, lock stays put
  const openTarget = (w.slice(0,i)+w.slice(i+1)); // what the 7 open slots must read
  ok(alphagram(openTarget)===alphagram(rack.join("")), "rack multiset == open slots of source word");
  // spelled check emulation
  const spelledFull = w.slice(0,i) + lock + w.slice(i+1);
  ok(spelledFull===w && I8.set.has(spelledFull), "reassembled full word validates");
}

// 6. drag insert-reorder math: moving order[oi] -> target yields expected sequence
function reorder(arr, oi, target){ arr=arr.slice(); const m=arr.splice(oi,1)[0]; arr.splice(target,0,m); return arr; }
ok(reorder(["a","b","c","d"],0,2).join("")==="bcad", "move first to index2");
ok(reorder(["a","b","c","d"],3,0).join("")==="dabc", "move last to front");
ok(reorder(["a","b","c","d"],1,1).join("")==="abcd", "no-op move");

console.log(fail? `\n${fail} CHECK(S) FAILED` : "\nALL CHECKS PASSED");
process.exit(fail?1:0);
