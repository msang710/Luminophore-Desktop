#ifndef LUMINOPHORE_GLOW_SHADER_H
#define LUMINOPHORE_GLOW_SHADER_H

static const char *luminophore_glow_vertex_source =
    "attribute vec2 a_position;"
    "void main(){gl_Position=vec4(a_position,0.0,1.0);}";

static const char *luminophore_glow_fragment_source =
    "precision highp float;"
    "uniform vec2 u_viewport; uniform float u_time; uniform float u_age;"
    "uniform vec4 u_rect; uniform float u_radius; uniform float u_outline;"
    "uniform float u_extent; uniform float u_intensity; uniform float u_phase;"
    "uniform vec3 u_base; uniform vec3 u_core;"
    "const float PI=3.141592653589793;"
    "float rounded_rect(vec2 p,vec2 h,float r){vec2 q=abs(p)-h+vec2(r);"
    "return min(max(q.x,q.y),0.0)+length(max(q,0.0))-r;}"
    "void main(){"
    " vec2 p=gl_FragCoord.xy-u_rect.xy; vec2 h=max(vec2(1.0),u_rect.zw*0.5);"
    " float d=rounded_rect(p,h,min(u_radius,min(h.x,h.y))); float x=max(d,0.0);"
    " float character=fract(u_phase*4.17+0.31);"
    " float period=mix(4.9,6.4,fract(u_phase*2.73+0.17));"
    " float cycle=fract(u_time/period+u_phase);"
    " float rise=mix(0.32,0.44,character);"
    " float breath=smoothstep(0.0,rise,cycle)*(1.0-smoothstep(rise,1.0,cycle));"
    " float radius_wave=0.5+0.5*sin(2.0*PI*(u_time/(period*1.37)+u_phase*1.37)+0.43);"
    " float extent=max(1.0,u_extent*mix(0.96,1.06,radius_wave));"
    " float inner_gain=mix(mix(0.82,0.88,character),mix(1.18,1.25,character),breath);"
    " float bloom_gain=mix(mix(0.30,0.46,character),mix(1.58,1.82,character),breath);"
    " float core=0.0;"
    " float near=exp(-5.298317367*pow(x/max(1.0,u_extent*0.32),2.0))*0.29*inner_gain;"
    " float bloom=exp(-5.298317367*pow(x/extent,2.0))*0.070*bloom_gain;"
    " float ignition=smoothstep(0.0,0.20,u_age);"
    " float a=(core+near+bloom)*u_intensity*ignition;"
    " a*=smoothstep(-max(1.0,u_outline),0.0,d)*(1.0-smoothstep(max(0.0,extent-6.0),extent,x));"
    " vec3 c=(u_core*core+u_base*(near+bloom))/max(0.00001,core+near+bloom);"
    " gl_FragColor=vec4(c*a,a);"
    "}";

#endif
