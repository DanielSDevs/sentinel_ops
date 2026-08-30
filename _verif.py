import django, os, time
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()
from apps.core.models import Incidente, FamiliaSinal
from apps.core.tempo import data_referencia
from django.db.models import Count

print("=== conferência contra o notebook ===")
print(f"  incidentes .......... {Incidente.objects.count():,} (esperado 122.543)")
print(f"  elegíveis KPI ....... {Incidente.objects.filter(entrou_kpi=True).count():,} (esperado 25.600)")
print(f"  violações ........... {Incidente.objects.filter(kpi_violado=True).count():,} (esperado 248)")
print(f"  com incidente pai ... {Incidente.objects.filter(incidente_pai__isnull=False).count():,}")
print(f"  referência temporal . {data_referencia()}")

print("\n=== cobertura da taxonomia de sinais ===")
tot = Incidente.objects.count()
for row in Incidente.objects.values('familia_sinal__nome').annotate(n=Count('id')).order_by('-n'):
    print(f"  {row['familia_sinal__nome'] or '(nulo)':26s} {row['n']:7,}  {row['n']/tot*100:5.1f}%")

print("\n=== camada de inferência (com tempo) ===")
from apps.intelligence.services import health, deltas, risk, anomaly, insights, briefing, correlation
from apps.intelligence.services.base import DIM

def cron(nome, fn):
    t = time.time(); r = fn(); ms = (time.time()-t)*1000
    print(f"  {nome:24s} {ms:7.0f} ms")
    return r

h = cron("health.calcular", health.calcular)
print(f"      -> score {h.score}/100 ({h.rotulo}) | ofensor: {h.principal_ofensor.nome if h.principal_ofensor else '-'}")
for f in h.fatores_ordenados:
    print(f"         {f.nome:34s} -{f.pontos_perdidos:5.1f} pts  ({f.valor_exibido})")

d = cron("deltas.calcular", deltas.calcular)
r = cron("risk.calcular(produto)", lambda: risk.calcular(DIM.PRODUTO))
print(f"      -> top risco: {r[0].chave} score={r[0].score} p={r[0].probabilidade:.3f}" if r else "      -> vazio")
a = cron("anomaly.detectar_por_dim", lambda: anomaly.detectar_por_dimensao(DIM.FAMILIA))
print(f"      -> {len(a)} anomalias")
i = cron("insights.gerar", insights.gerar)
for x in i:
    print(f"         [{x.prioridade:3d}] {x.titulo[:70]}")
b = cron("briefing.montar", briefing.montar)
t = cron("correlation.tempestades", lambda: correlation.tempestades(limite=3))
print(f"      -> maior blast radius: {t[0].total_filhos} filhos" if t else "")
c = cron("correlation.cadeia", lambda: correlation.cadeia_de_sintomas(limite=5))
for p in c[:5]:
    print(f"         {p['de']} -> {p['para']}: {p['ocorrencias']}x ({p['participacao']}%)")
